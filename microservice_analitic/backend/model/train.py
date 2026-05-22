"""Обучение CatBoost: walk-forward split, grid search, финальное обучение."""
from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

try:
    import catboost as cb
except ImportError as exc:
    raise ImportError("Установи catboost: pip install catboost") from exc

from sklearn.model_selection import TimeSeriesSplit

from backend.dataset.core import log

_LOG = logging.getLogger(__name__)

from .config import (
    CV_SPLITS,
    DEVICES,
    EARLY_STOPPING_ROUNDS,
    FINAL_EARLY_STOPPING_ROUNDS,
    GPU_RAM_PART,
    MODELS_DIR,
    PARAM_GRID,
    RANDOM_SEED,
    TASK_TYPE,
    TRAIN_FRACTION,
    VERBOSE_TRAIN,
)
from .metrics import (
    compute_direction_metrics,
    compute_metrics,
    compute_signal_metrics,
    compute_trading_metrics,
)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

_CPU_THREAD_LIMIT_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OMP_THREAD_LIMIT",
    "MKL_NUM_THREADS",
    "TBB_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)

_CPU_DYNAMIC_ENV_VARS: dict[str, str] = {
    "OMP_DYNAMIC": "FALSE",
    "MKL_DYNAMIC": "FALSE",
}


def walk_forward_split(n: int, train_fraction: float = TRAIN_FRACTION) -> tuple[int, int]:
    """Возвращает (train_size, test_size) для временно́го разбиения без перемешивания."""
    train_size = int(n * train_fraction)
    return train_size, n - train_size


_CV_MODES: frozenset[str] = frozenset({"expanding", "rolling"})


def _build_cv_splitter(
    cv_mode: str,
    max_train_size: int | None,
    target_horizon_bars: int,
    n_splits: int = CV_SPLITS,
) -> TimeSeriesSplit:
    """Возвращает настроенный TimeSeriesSplit для выбранного режима.

    cv_mode:
        "expanding" (по умолчанию) — каждый фолд добавляет данные слева, train-окно растёт.
        "rolling"                 — train-окно фиксированного размера max_train_size
                                    (если None, sklearn сам определяет разумный предел;
                                    см. TimeSeriesSplit.max_train_size).

    target_horizon_bars — gap между train/val (purge gap против утечки).
    """
    if cv_mode not in _CV_MODES:
        raise ValueError(
            f"cv_mode должен быть одним из {sorted(_CV_MODES)}, получено {cv_mode!r}"
        )
    mts = max_train_size if cv_mode == "rolling" else None
    if mts is not None and mts <= 0:
        mts = None
    return TimeSeriesSplit(n_splits=n_splits, gap=target_horizon_bars, max_train_size=mts)


def _build_pool(
    X: pd.DataFrame,
    y: pd.Series | None = None,
) -> "cb.Pool":
    """Создаёт CatBoost Pool из DataFrame (NaN сохраняются как missing values)."""
    return cb.Pool(
        data=X.values,
        label=y.values if y is not None else None,
        feature_names=list(X.columns),
    )


def _prepare_model_params(params: dict[str, Any], *, use_gpu: bool) -> dict[str, Any]:
    """Подготавливает параметры CatBoost под выбранное устройство.

    border_count (число бинов квантования) поддерживается и на CPU, и на GPU —
    удалять его нельзя: без него CatBoost CPU использует дефолтное значение 254,
    что при depth=10 удваивает рабочий набор гистограмм и гарантирует
    memory-bandwidth bottleneck даже для комбо с border_count=128 из сетки.
    """
    result = dict(params)
    if not use_gpu:
        result.pop("gpu_ram_part", None)
        result.pop("devices", None)
    return result


def _get_full_cpu_thread_count() -> int:
    """Возвращает число потоков для загрузки всех доступных CPU."""
    try:
        return max(len(os.sched_getaffinity(0)), 1)
    except (AttributeError, OSError):
        return max(os.cpu_count() or 1, 1)


def _configure_full_cpu_runtime(thread_count: int) -> None:
    """Снимает внешние лимиты потоков и выставляет полную загрузку CPU."""
    for env_name in _CPU_THREAD_LIMIT_ENV_VARS:
        os.environ[env_name] = str(thread_count)
    for env_name, env_value in _CPU_DYNAMIC_ENV_VARS.items():
        os.environ[env_name] = env_value


def _make_model(
    params: dict[str, Any],
    *,
    verbose: int = 0,
    use_gpu: bool = True,
    early_stopping_rounds: int = EARLY_STOPPING_ROUNDS,
) -> "cb.CatBoostRegressor":
    """Собирает CatBoostRegressor с нужными параметрами."""
    p = _prepare_model_params(params, use_gpu=use_gpu)

    model_params: dict[str, Any] = {
        **p,
        "loss_function":        "RMSE",
        "eval_metric":          "RMSE",
        "random_seed":          RANDOM_SEED,
        "early_stopping_rounds": early_stopping_rounds,
        "verbose":              verbose,
    }
    if use_gpu:
        model_params["task_type"]    = TASK_TYPE
        model_params["devices"]      = DEVICES
        model_params["gpu_ram_part"] = GPU_RAM_PART
    else:
        # _configure_full_cpu_runtime вызывается один раз перед циклом в
        # grid_search_cv / train_final_model, а не на каждый fold.
        cpu_threads = _get_full_cpu_thread_count()
        model_params["task_type"]   = "CPU"
        model_params["thread_count"] = cpu_threads
    return cb.CatBoostRegressor(**model_params)


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


# ---------------------------------------------------------------------------
# Финальное обучение на train, оценка на test
# ---------------------------------------------------------------------------

def train_final_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    best_params: dict[str, Any],
    *,
    annualize_factor: float = 1.0,
    use_gpu: bool = True,
) -> tuple["cb.CatBoostRegressor", dict[str, float], np.ndarray]:
    """Обучает финальную модель на полном train-наборе и оценивает на test.

    Возвращает:
        model    — обученный CatBoostRegressor
        metrics  — словарь {MAE, RMSE, R2, Sharpe} на тестовых данных
        y_pred   — np.ndarray предсказаний на тесте
    """
    # --- Валидация входных данных ---
    if len(X_train) == 0 or len(y_train) == 0:
        raise ValueError("[train] X_train / y_train пустые")
    if len(X_test) == 0 or len(y_test) == 0:
        raise ValueError("[train] X_test / y_test пустые")

    train_params = _prepare_model_params(best_params, use_gpu=use_gpu)
    log(f"[train] Финальное обучение: {len(X_train)} train строк, "
        f"{len(X_test)} test строк, {len(X_train.columns)} признаков, "
        f"params={train_params}, device={'GPU' if use_gpu else 'CPU'}")
    if not use_gpu:
        cpu_threads = _get_full_cpu_thread_count()
        _configure_full_cpu_runtime(cpu_threads)
        log(f"[train] CPU thread_count={cpu_threads} (без ограничений, env vars выставлены)")

    model = _make_model(train_params, verbose=VERBOSE_TRAIN, use_gpu=use_gpu,
                        early_stopping_rounds=FINAL_EARLY_STOPPING_ROUNDS)
    model.fit(
        _build_pool(X_train, y_train),
        eval_set=_build_pool(X_test, y_test),
        use_best_model=True,
    )
    try:
        _best_iter = model.get_best_iteration()
        log(f"[train] best_iteration = {_best_iter}")
    except Exception as _e:
        log(f"[train] best_iteration unavailable: {_e}")

    y_pred = model.predict(X_test.values)
    metrics = compute_metrics(y_test.values, y_pred, annualize_factor=annualize_factor)
    trading = compute_trading_metrics(y_test.values, y_pred, annualize_factor=annualize_factor)
    # Удаляем дубль "Sharpe" (капитализированный) из compute_metrics —
    # оставляем только "sharpe" (нижний регистр) из compute_trading_metrics,
    # чтобы в словаре метрик не было двух ключей с одним значением.
    metrics.pop("Sharpe", None)
    metrics.update(trading)

    # Сигнальные метрики (binary и 3-class при deadband)
    signal_m = compute_signal_metrics(y_test.values, y_pred)
    # Плоские скаляры — добавляем напрямую (для CSV/JSON)
    metrics["binary_accuracy"]  = signal_m["binary_accuracy"]
    metrics["binary_precision"] = signal_m["binary_precision"]
    metrics["binary_recall"]    = signal_m["binary_recall"]
    metrics["binary_f1"]        = signal_m["binary_f1"]
    metrics["binary_mcc"]       = signal_m["binary_mcc"]
    # Полные данные (confusion matrix, per-class) — отдельным ключом
    metrics["signal_details"]   = signal_m

    _dir_acc = trading.get("dir_acc_pct", 0.0)
    _r2      = metrics.get("R2", 0.0)
    if _r2 < 0:
        _LOG.warning("[train] R² = %.4f < 0 — модель хуже наивного среднего!", _r2)
    if _dir_acc < 50.0:
        _LOG.warning("[train] Dir.Acc = %.1f%% < 50%% — направление хуже случайного!", _dir_acc)

    log(f"[train] Тест-метрики: MAE={metrics['MAE']:.6f}  RMSE={metrics['RMSE']:.6f}  "
        f"R2={_r2:.4f}  sharpe={trading['sharpe']:.4f}  "
        f"Dir.Acc={_dir_acc:.1f}%  PF={trading['profit_factor']:.4f}  "
        f"MCC={signal_m['binary_mcc']:.4f}  F1={signal_m['binary_f1']:.4f}")
    return model, metrics, y_pred


# ---------------------------------------------------------------------------
# Сохранение модели
# ---------------------------------------------------------------------------

def save_model(
    model: "cb.CatBoostRegressor",
    symbol: str,
    timeframe: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> Path:
    """Сохраняет модель в models/{symbol}_{timeframe}.cbm."""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / f"catboost_{symbol.lower()}_{timeframe.lower()}.cbm"
    model.save_model(str(path))
    log(f"[save] Модель сохранена: {path}")
    return path


# ---------------------------------------------------------------------------
# Диагностика переобучения финальной модели
# ---------------------------------------------------------------------------

def compute_overfitting_diagnostics(
    model: "cb.CatBoostRegressor",
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    *,
    feature_cols: list[str],
    step_ms: int = 3_600_000,
) -> dict[str, Any]:
    """Диагностика переобучения финальной модели по четырём критериям.

    Проверки:
        1. Learning curve — val RMSE по итерациям из evals_result + train RMSE
           в best_iteration как горизонтальная линия для сравнения.
        2. R² gap — (R²_train − R²_test) / |R²_train|; флаг если >20%.
        3. Walk-forward last month — метрики модели на последних ~30 днях
           тест-выборки (самые свежие данные, уже отложенные).
        4. Feature importance concentration — сумма важности топ-5 признаков;
           флаг если >30% от общего итога (100%).

    Аргументы:
        step_ms — длительность одного бара в миллисекундах (для расчёта «месяца»).

    Возвращает словарь с данными для отображения в UI.
    """
    # ---- 1. Learning curve ----------------------------------------
    evals = model.get_evals_result()
    lc_val_rmse: list[float] = []
    lc_iters:    list[int]   = []

    # CatBoost именует первый eval_set «validation» (или «validation_0»/«learn»)
    val_key = next(
        (k for k in evals if "validation" in k.lower()),
        next(iter(evals), None),
    )
    if val_key and "RMSE" in evals[val_key]:
        rmse_arr = evals[val_key]["RMSE"]
        n = len(rmse_arr)
        subsample_step = max(1, n // 500)
        lc_iters    = list(range(0, n, subsample_step))
        lc_val_rmse = [rmse_arr[i] for i in lc_iters]

    try:
        best_iter = int(model.get_best_iteration() or 0)
        log(f"[overfit] best_iteration = {best_iter}")
    except Exception as _e:
        log(f"[overfit] best_iteration unavailable: {_e}")
        best_iter = 0

    # Train RMSE при best_iteration (предсказание уже учитывает use_best_model)
    y_train_arr  = np.asarray(y_train, dtype=float)
    y_pred_train = model.predict(X_train.values)
    train_rmse   = float(np.sqrt(np.mean((y_train_arr - y_pred_train) ** 2)))

    # ---- 2. Train / Test R² gap -----------------------------------
    ss_res_tr = np.sum((y_train_arr - y_pred_train) ** 2)
    ss_tot_tr = np.sum((y_train_arr - np.mean(y_train_arr)) ** 2)
    r2_train  = float(1.0 - ss_res_tr / ss_tot_tr) if ss_tot_tr > 1e-15 else 0.0

    y_test_arr  = np.asarray(y_test, dtype=float)
    y_pred_test = model.predict(X_test.values)
    ss_res_te   = np.sum((y_test_arr - y_pred_test) ** 2)
    ss_tot_te   = np.sum((y_test_arr - np.mean(y_test_arr)) ** 2)
    r2_test     = float(1.0 - ss_res_te / ss_tot_te) if ss_tot_te > 1e-15 else 0.0

    r2_gap_pct      = float((r2_train - r2_test) / abs(r2_train) * 100.0) if abs(r2_train) > 1e-6 else 0.0
    r2_overfit_flag = r2_gap_pct > 20.0

    # ---- 3. Walk-forward last month -------------------------------
    bars_per_month = max(1, int(30 * 24 * 3600 * 1000 / step_ms))
    wf_n = min(bars_per_month, max(1, len(X_test) - 1))
    X_wf      = X_test.iloc[-wf_n:]
    y_wf      = y_test.iloc[-wf_n:]
    y_wf_pred = model.predict(X_wf.values)
    y_wf_arr  = np.asarray(y_wf, dtype=float)

    wf_rmse   = float(np.sqrt(np.mean((y_wf_arr - y_wf_pred) ** 2)))
    wf_ss_res = np.sum((y_wf_arr - y_wf_pred) ** 2)
    wf_ss_tot = np.sum((y_wf_arr - np.mean(y_wf_arr)) ** 2)
    wf_r2     = float(1.0 - wf_ss_res / wf_ss_tot) if wf_ss_tot > 1e-15 else 0.0
    wf_dir    = compute_direction_metrics(y_wf_arr, y_wf_pred)
    wf_trade  = compute_trading_metrics(y_wf_arr, y_wf_pred)

    # ---- 4. Feature importance concentration ----------------------
    fi_raw   = model.get_feature_importance()  # суммируется в 100.0
    fi_series = pd.Series(fi_raw, index=feature_cols).sort_values(ascending=False)
    top5      = fi_series.head(5)
    fi_top5_sum   = float(top5.sum())
    fi_top5_names = top5.index.tolist()
    fi_top5_vals  = top5.values.tolist()
    fi_conc_flag  = fi_top5_sum > 50.0

    log(
        f"[overfit] R²: train={r2_train:.4f}  test={r2_test:.4f}  "
        f"gap={r2_gap_pct:.1f}%"
        + ("  ⚠ >20% переобучение" if r2_overfit_flag else "  ✓")
    )
    log(
        f"[overfit] Walk-forward последние {wf_n} баров: "
        f"RMSE={wf_rmse:.6f}  R²={wf_r2:.4f}  "
        f"Dir.Acc={wf_dir['accuracy'] * 100:.1f}%  Sharpe={wf_trade['sharpe']:.4f}"
    )
    log(
        f"[overfit] FI топ-5 сумма={fi_top5_sum:.1f}%"
        + ("  ⚠ >50% концентрация" if fi_conc_flag else "  ✓")
    )

    return {
        "learning_curve": {
            "iterations":         lc_iters,
            "val_rmse":           lc_val_rmse,
            "train_rmse_at_best": train_rmse,
            "best_iteration":     best_iter,
        },
        "r2_train":            r2_train,
        "r2_test":             r2_test,
        "r2_gap_pct":          r2_gap_pct,
        "r2_overfit_flag":     r2_overfit_flag,
        "wf_bars":             wf_n,
        "wf_rmse":             wf_rmse,
        "wf_r2":               wf_r2,
        "wf_dir_acc_pct":      float(wf_dir["accuracy"] * 100.0),
        "wf_sharpe":           wf_trade["sharpe"],
        "fi_top5_sum_pct":     fi_top5_sum,
        "fi_top5_names":       fi_top5_names,
        "fi_top5_values":      fi_top5_vals,
        "fi_concentration_flag": fi_conc_flag,
    }
