"""Поиск гиперпараметров CatBoost: grid search и Optuna (TPE) с TimeSeriesSplit."""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Callable

import numpy as np
import pandas as pd

try:
    import catboost as cb  # noqa: F401  (используется в аннотациях типов)
except ImportError as exc:
    raise ImportError("Установи catboost: pip install catboost") from exc

from backend.dataset.core import log

_LOG = logging.getLogger(__name__)

from .config import (
    CV_SPLITS,
    PARAM_GRID,
    RANDOM_SEED,
)
from .metrics import (
    compute_direction_metrics,
    compute_signal_metrics,
    compute_trading_metrics,
)
from .train_base import (
    _build_cv_splitter,
    _build_pool,
    _configure_full_cpu_runtime,
    _get_full_cpu_thread_count,
    _make_model,
    _prepare_model_params,
)


# ---------------------------------------------------------------------------
# Grid search с TimeSeriesSplit
# ---------------------------------------------------------------------------

def grid_search_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    use_gpu: bool = True,
    param_grid: list[dict] | None = None,
    on_combo_done: Callable[[int, int, dict], None] | None = None,
    annualize_factor: float = 1.0,
    target_horizon_bars: int = 0,
    cv_mode: str = "expanding",
    max_train_size: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Перебирает param_grid через TimeSeriesSplit, возвращает лучшие params и таблицу результатов.

    Аргументы:
        param_grid           — список комбинаций гиперпараметров; если None, используется PARAM_GRID.
        on_combo_done        — опциональный callback(combo_idx, total, row_dict) после каждой комбинации.
        annualize_factor     — количество баров в году для аннуализации Sharpe (передавать bars_per_year).
        target_horizon_bars  — горизонт прогноза в барах; используется как gap между train/val фолдами
                               (purge gap, метод Lopez de Prado) для устранения утечки через целевую
                               переменную. 0 = без gap (старое поведение).
        cv_mode              — "expanding" (по умолчанию) или "rolling". Rolling режим
                               ограничивает размер train-окна max_train_size.
        max_train_size       — размер скользящего train-окна в барах (только для cv_mode="rolling").
                               None / ≤0 → без ограничения (поведение expanding).

    Возвращает:
        best_params  — словарь гиперпараметров с наименьшим mean RMSE по фолдам
        grid_df      — DataFrame со всеми результатами, включая TP/TN/FP/FN/accuracy
    """
    grid_source = param_grid if param_grid is not None else PARAM_GRID

    # --- Валидация входных данных ---
    if len(X_train) == 0 or len(y_train) == 0:
        raise ValueError("[grid_search] X_train / y_train пустые — нечего обучать")
    if len(X_train) != len(y_train):
        raise ValueError(
            f"[grid_search] Размеры не совпадают: X_train={len(X_train)}, y_train={len(y_train)}"
        )
    nan_x = int(pd.DataFrame(X_train).isnull().any(axis=1).sum())
    nan_y = int(pd.Series(y_train).isnull().sum())
    if nan_x > 0:
        _LOG.warning("[grid_search] X_train содержит %d строк с NaN", nan_x)
    if nan_y > 0:
        _LOG.warning("[grid_search] y_train содержит %d NaN значений", nan_y)
    if target_horizon_bars > 0:
        log(f"[grid_search] purge gap = {target_horizon_bars} баров между train/val фолдами")
    if use_gpu:
        grid_used = [dict(params) for params in grid_source]
    else:
        grid_used = []
        seen: set[tuple[tuple[str, Any], ...]] = set()
        for params in grid_source:
            prepared = _prepare_model_params(params, use_gpu=False)
            key = tuple(sorted(prepared.items()))
            if key in seen:
                continue
            seen.add(key)
            grid_used.append(prepared)
    tscv = _build_cv_splitter(cv_mode, max_train_size, target_horizon_bars)
    results: list[dict] = []

    _cv_suffix = (
        f"rolling max_train={max_train_size}" if cv_mode == "rolling" and max_train_size
        else cv_mode
    )
    log(f"[grid_search] {len(grid_used)} комбинаций × {CV_SPLITS} folds "
        f"(device={'GPU' if use_gpu else 'CPU'}, gap={target_horizon_bars} bars, "
        f"cv={_cv_suffix})")
    if not use_gpu:
        cpu_threads = _get_full_cpu_thread_count()
        _configure_full_cpu_runtime(cpu_threads)
        log(f"[grid_search] CPU thread_count={cpu_threads} (без ограничений, env vars выставлены)")

    for combo_idx, params in enumerate(grid_used, start=1):
        fold_rmse: list[float] = []
        all_y_val: list[float] = []
        all_y_pred: list[float] = []
        t0 = time.perf_counter()

        for fold_num, (tr_idx, val_idx) in enumerate(tscv.split(X_train), start=1):
            X_tr, X_val = X_train.iloc[tr_idx], X_train.iloc[val_idx]
            y_tr, y_val = y_train.iloc[tr_idx], y_train.iloc[val_idx]

            if len(X_tr) == 0 or len(X_val) == 0:
                _LOG.warning(
                    "[grid_search] combo #%d fold #%d: пустой train (%d) или val (%d) — пропуск",
                    combo_idx, fold_num, len(X_tr), len(X_val),
                )
                continue

            try:
                model = _make_model(params, use_gpu=use_gpu)
                model.fit(
                    _build_pool(X_tr, y_tr),
                    eval_set=_build_pool(X_val, y_val),
                    use_best_model=True,
                )

                y_pred = model.predict(X_val.values)
                rmse = float(np.sqrt(np.mean((np.asarray(y_val) - y_pred) ** 2)))
                fold_rmse.append(rmse)
                all_y_val.extend(y_val.tolist())
                all_y_pred.extend(y_pred.tolist())
            except Exception as _fold_exc:
                _LOG.error(
                    "[grid_search] combo #%d fold #%d FAILED: %s\n%s",
                    combo_idx, fold_num, _fold_exc, traceback.format_exc(),
                )

        if not fold_rmse:
            _LOG.error("[grid_search] combo #%d: все фолды провалились — пропуск", combo_idx)
            continue

        mean_rmse = float(np.mean(fold_rmse))
        std_rmse = float(np.std(fold_rmse))
        elapsed = time.perf_counter() - t0
        dir_metrics     = compute_direction_metrics(all_y_val, all_y_pred)
        trading_metrics = compute_trading_metrics(all_y_val, all_y_pred,
                                                  annualize_factor=annualize_factor)
        signal_metrics  = compute_signal_metrics(all_y_val, all_y_pred)

        row = {
            "combo": combo_idx,
            **params,
            "mean_rmse_cv":  mean_rmse,
            "std_rmse_cv":   std_rmse,
            "sharpe":        trading_metrics["sharpe"],
            "dir_acc_pct":   trading_metrics["dir_acc_pct"],
            "mae_pct":       trading_metrics["mae_pct"],
            "profit_factor": trading_metrics["profit_factor"],
            "TP":       dir_metrics["TP"],
            "TN":       dir_metrics["TN"],
            "FP":       dir_metrics["FP"],
            "FN":       dir_metrics["FN"],
            "accuracy": dir_metrics["accuracy"],
            "binary_mcc":       signal_metrics["binary_mcc"],
            "binary_f1":        signal_metrics["binary_f1"],
            "binary_precision": signal_metrics["binary_precision"],
            "binary_recall":    signal_metrics["binary_recall"],
            "elapsed_s": round(elapsed, 1),
        }
        results.append(row)
        if on_combo_done is not None:
            on_combo_done(combo_idx, len(grid_used), row)
        log(
            f"[grid_search] #{combo_idx:02d}/{len(grid_used)}  "
            f"rmse_cv={mean_rmse:.6f} ± {std_rmse:.6f}  "
            f"sharpe={trading_metrics['sharpe']:.4f}  "
            f"dir_acc={trading_metrics['dir_acc_pct']:.1f}%  "
            f"accuracy={dir_metrics['accuracy']:.4f}  "
            f"mcc={signal_metrics['binary_mcc']:.4f}  "
            f"f1={signal_metrics['binary_f1']:.4f}  "
            f"params={params}  ({elapsed:.1f}s)"
        )

    # Сортируем по Sharpe (убыв.), RMSE используем как вторичный критерий
    grid_df = (
        pd.DataFrame(results)
        .sort_values(["sharpe", "mean_rmse_cv"], ascending=[False, True])
        .reset_index(drop=True)
    )
    best_combo_idx = int(grid_df.iloc[0]["combo"])
    best_params = grid_used[best_combo_idx - 1].copy()

    log(f"[grid_search] Лучшие params (combo #{best_combo_idx}): {best_params}  "
        f"sharpe={grid_df.iloc[0]['sharpe']:.4f}  "
        f"rmse_cv={grid_df.iloc[0]['mean_rmse_cv']:.6f}")
    return best_params, grid_df


# ---------------------------------------------------------------------------
# Байесовский поиск (Optuna TPE) с TimeSeriesSplit
# ---------------------------------------------------------------------------

def optuna_search_cv(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    n_trials: int = 50,
    use_gpu: bool = True,
    search_space: "dict | None" = None,
    on_trial_done: Callable[[int, int, dict], None] | None = None,
    annualize_factor: float = 1.0,
    target_horizon_bars: int = 0,
    seed: int = RANDOM_SEED,
    cv_mode: str = "expanding",
    max_train_size: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Подбор гиперпараметров через Optuna TPE с walk-forward CV.

    Совместим с grid_search_cv по формату возврата: (best_params, trials_df),
    где trials_df отсортирован по sharpe ↓, mean_rmse_cv ↑, а схема колонок
    идентична grid_search_cv (combo, ..., mean_rmse_cv, sharpe, dir_acc_pct и т.д.).

    Аргументы:
        n_trials       — число trial-ов Optuna (по умолчанию 50).
        search_space   — опциональная замена диапазонов поиска. Словарь вида
                         {"iterations": (500, 10000), "depth": (4, 10),
                          "learning_rate": (0.005, 0.1), "l2_leaf_reg": (1.0, 10.0),
                          "bagging_temperature": (0.0, 2.0),
                          "border_count": [128, 254]}.
                         Если None — используются разумные диапазоны на базе
                         DEFAULT_PARAM_VALUES.
        on_trial_done  — callback(trial_idx, total, row_dict) после каждого trial.
        annualize_factor — количество баров в году для Sharpe.
        target_horizon_bars — purge gap между train/val фолдами (как в grid_search_cv).

    Требует пакет optuna>=3.6 (импортируется лениво; при отсутствии —
    выбрасывает ImportError с инструкцией).
    """
    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError as exc:
        raise ImportError(
            "Для Optuna-поиска установите пакет: pip install 'optuna>=3.6'"
        ) from exc

    # --- Валидация входных данных ---
    if len(X_train) == 0 or len(y_train) == 0:
        raise ValueError("[optuna] X_train / y_train пустые — нечего обучать")
    if len(X_train) != len(y_train):
        raise ValueError(
            f"[optuna] Размеры не совпадают: X_train={len(X_train)}, y_train={len(y_train)}"
        )
    nan_x = int(pd.DataFrame(X_train).isnull().any(axis=1).sum())
    nan_y = int(pd.Series(y_train).isnull().sum())
    if nan_x > 0:
        _LOG.warning("[optuna] X_train содержит %d строк с NaN", nan_x)
    if nan_y > 0:
        _LOG.warning("[optuna] y_train содержит %d NaN значений", nan_y)

    _cv_suffix = (
        f"rolling max_train={max_train_size}" if cv_mode == "rolling" and max_train_size
        else cv_mode
    )
    log(
        f"[optuna] TPE search: n_trials={n_trials} × {CV_SPLITS} folds "
        f"(device={'GPU' if use_gpu else 'CPU'}, gap={target_horizon_bars} bars, "
        f"cv={_cv_suffix})"
    )
    if not use_gpu:
        cpu_threads = _get_full_cpu_thread_count()
        _configure_full_cpu_runtime(cpu_threads)
        log(f"[optuna] CPU thread_count={cpu_threads} (без ограничений, env vars выставлены)")

    # Диапазоны поиска — значения по умолчанию на основе DEFAULT_PARAM_VALUES
    # iterations: расширено до 15000, early_stopping контролирует фактический предел.
    _space = search_space or {}
    iter_range  = _space.get("iterations",          (500,   15_000))
    depth_range = _space.get("depth",               (4,     10))
    lr_range    = _space.get("learning_rate",       (0.005, 0.1))
    l2_range    = _space.get("l2_leaf_reg",         (1.0,   10.0))
    bag_range   = _space.get("bagging_temperature", (0.0,   2.0))
    border_opts = list(_space.get("border_count",   [128, 254]))

    tscv = _build_cv_splitter(cv_mode, max_train_size, target_horizon_bars)
    results: list[dict] = []

    def _objective(trial: "optuna.trial.Trial") -> float:
        params: dict[str, Any] = {
            "iterations":          trial.suggest_int(
                "iterations", int(iter_range[0]), int(iter_range[1]), step=500
            ),
            "depth":               trial.suggest_int(
                "depth", int(depth_range[0]), int(depth_range[1])
            ),
            "learning_rate":       trial.suggest_float(
                "learning_rate", float(lr_range[0]), float(lr_range[1]), log=True
            ),
            "l2_leaf_reg":         trial.suggest_float(
                "l2_leaf_reg", float(l2_range[0]), float(l2_range[1])
            ),
            "bagging_temperature": trial.suggest_float(
                "bagging_temperature", float(bag_range[0]), float(bag_range[1])
            ),
        }
        if use_gpu and border_opts:
            params["border_count"] = trial.suggest_categorical("border_count", border_opts)

        fold_rmse: list[float] = []
        all_y_val: list[float] = []
        all_y_pred: list[float] = []
        t0 = time.perf_counter()

        for fold_num, (tr_idx, val_idx) in enumerate(tscv.split(X_train), start=1):
            X_tr, X_val = X_train.iloc[tr_idx], X_train.iloc[val_idx]
            y_tr, y_val = y_train.iloc[tr_idx], y_train.iloc[val_idx]
            if len(X_tr) == 0 or len(X_val) == 0:
                continue
            try:
                model = _make_model(params, use_gpu=use_gpu)
                model.fit(
                    _build_pool(X_tr, y_tr),
                    eval_set=_build_pool(X_val, y_val),
                    use_best_model=True,
                )
                y_pred = model.predict(X_val.values)
                rmse = float(np.sqrt(np.mean((np.asarray(y_val) - y_pred) ** 2)))
                fold_rmse.append(rmse)
                all_y_val.extend(y_val.tolist())
                all_y_pred.extend(y_pred.tolist())
            except Exception as _fold_exc:
                _LOG.error(
                    "[optuna] trial #%d fold #%d FAILED: %s\n%s",
                    trial.number + 1, fold_num, _fold_exc, traceback.format_exc(),
                )

        if not fold_rmse:
            raise optuna.TrialPruned("все фолды провалились")

        mean_rmse = float(np.mean(fold_rmse))
        std_rmse  = float(np.std(fold_rmse))
        elapsed   = time.perf_counter() - t0
        dir_metrics     = compute_direction_metrics(all_y_val, all_y_pred)
        trading_metrics = compute_trading_metrics(
            all_y_val, all_y_pred, annualize_factor=annualize_factor
        )
        signal_metrics  = compute_signal_metrics(all_y_val, all_y_pred)

        row = {
            "combo": trial.number + 1,
            **params,
            "mean_rmse_cv":  mean_rmse,
            "std_rmse_cv":   std_rmse,
            "sharpe":        trading_metrics["sharpe"],
            "dir_acc_pct":   trading_metrics["dir_acc_pct"],
            "mae_pct":       trading_metrics["mae_pct"],
            "profit_factor": trading_metrics["profit_factor"],
            "TP":       dir_metrics["TP"],
            "TN":       dir_metrics["TN"],
            "FP":       dir_metrics["FP"],
            "FN":       dir_metrics["FN"],
            "accuracy": dir_metrics["accuracy"],
            "binary_mcc":       signal_metrics["binary_mcc"],
            "binary_f1":        signal_metrics["binary_f1"],
            "binary_precision": signal_metrics["binary_precision"],
            "binary_recall":    signal_metrics["binary_recall"],
            "elapsed_s": round(elapsed, 1),
        }
        results.append(row)
        if on_trial_done is not None:
            on_trial_done(trial.number + 1, n_trials, row)
        log(
            f"[optuna] trial #{trial.number + 1:03d}/{n_trials}  "
            f"rmse_cv={mean_rmse:.6f}  sharpe={trading_metrics['sharpe']:.4f}  "
            f"mcc={signal_metrics['binary_mcc']:.4f}  f1={signal_metrics['binary_f1']:.4f}  "
            f"params={params}  ({elapsed:.1f}s)"
        )
        return trading_metrics["sharpe"]

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=seed))
    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)

    if not results:
        raise RuntimeError("[optuna] Ни один trial не завершился успешно")

    trials_df = (
        pd.DataFrame(results)
        .sort_values(["sharpe", "mean_rmse_cv"], ascending=[False, True])
        .reset_index(drop=True)
    )
    top_row = trials_df.iloc[0].to_dict()
    _param_keys = [
        "iterations", "depth", "learning_rate",
        "l2_leaf_reg", "bagging_temperature",
    ]
    if use_gpu and "border_count" in top_row:
        _param_keys.append("border_count")
    best_params: dict[str, Any] = {}
    for k in _param_keys:
        if k not in top_row:
            continue
        v = top_row[k]
        if k in ("iterations", "depth", "border_count") and not pd.isna(v):
            v = int(v)
        best_params[k] = v

    log(
        f"[optuna] Лучший trial #{int(top_row['combo'])}: params={best_params}  "
        f"sharpe={trials_df.iloc[0]['sharpe']:.4f}  "
        f"rmse_cv={trials_df.iloc[0]['mean_rmse_cv']:.6f}"
    )
    return best_params, trials_df
