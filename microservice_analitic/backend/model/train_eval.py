"""Финальное обучение CatBoost, сохранение модели и диагностика переобучения."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import catboost as cb  # noqa: F401  (используется в аннотациях типов)
except ImportError as exc:
    raise ImportError("Установи catboost: pip install catboost") from exc

from backend.dataset.core import log

_LOG = logging.getLogger(__name__)

from .config import (
    FINAL_EARLY_STOPPING_ROUNDS,
    MODELS_DIR,
    VERBOSE_TRAIN,
)
from .metrics import (
    compute_direction_metrics,
    compute_metrics,
    compute_signal_metrics,
    compute_trading_metrics,
)
from .train_base import (
    _build_pool,
    _configure_full_cpu_runtime,
    _get_full_cpu_thread_count,
    _make_model,
    _prepare_model_params,
)


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
