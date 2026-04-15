from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.utils import _drop_non_model_columns
from catboost_floader.evaluation.backtest import build_direct_baselines


OVERFIT_FIELDS = [
    "train_MAE",
    "val_MAE",
    "holdout_MAE",
    "train_sign_acc",
    "train_sign_acc_pct",
    "val_sign_acc",
    "val_sign_acc_pct",
    "holdout_sign_acc",
    "holdout_sign_acc_pct",
    "mae_gap_train_val",
    "mae_gap_train_holdout",
    "sign_gap_train_val",
    "sign_gap_train_holdout",
    "mae_overfit_ratio",
    "holdout_overfit_ratio",
    "overfit_status",
    "overfit_reason",
    "train_delta_vs_baseline",
    "val_delta_vs_baseline",
    "holdout_delta_vs_baseline",
]


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(out):
        return None
    return out


def _accuracy_pct(value: Any) -> float | None:
    value_f = _safe_float(value)
    if value_f is None:
        return None
    return round(value_f * 100.0, 2)


def _split_direct_metrics(
    direct_model,
    X_split: pd.DataFrame,
    y_split: pd.DataFrame,
) -> Dict[str, float | None]:
    if X_split.empty or y_split.empty:
        return {"MAE": None, "sign_acc": None, "delta_vs_baseline": None}

    if "close" not in X_split.columns or "target_future_close" not in y_split.columns or "target_return" not in y_split.columns:
        return {"MAE": None, "sign_acc": None, "delta_vs_baseline": None}

    X_model = _drop_non_model_columns(X_split)
    if X_model.empty:
        return {"MAE": None, "sign_acc": None, "delta_vs_baseline": None}

    pred_details = direct_model.predict_details(X_model)
    pred_return = np.asarray(pred_details.get("pred_return"), dtype=float)

    close = pd.to_numeric(X_split["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_price = pd.to_numeric(y_split["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_return = pd.to_numeric(y_split["target_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float)

    n = min(len(close), len(target_price), len(target_return), len(pred_return))
    if n <= 0:
        return {"MAE": None, "sign_acc": None, "delta_vs_baseline": None}

    close = close[:n]
    target_price = target_price[:n]
    target_return = target_return[:n]
    pred_return = pred_return[:n]

    pred_price = close * (1.0 + pred_return)
    mae = float(np.mean(np.abs(target_price - pred_price)))
    sign_acc = float(np.mean(np.sign(target_return) == np.sign(pred_return)))

    baseline_df = build_direct_baselines(X_split.iloc[:n].reset_index(drop=True))
    persistence_return = pd.to_numeric(
        baseline_df.get("baseline_persistence_return", pd.Series(np.zeros(n))),
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=float)
    persistence_price = close * (1.0 + persistence_return)
    persistence_mae = float(np.mean(np.abs(target_price - persistence_price)))
    delta_vs_baseline = float(persistence_mae - mae)

    return {
        "MAE": mae,
        "sign_acc": sign_acc,
        "delta_vs_baseline": delta_vs_baseline,
    }


def _resolve_overfit_status(payload: Dict[str, float | None]) -> tuple[str, str]:
    mae_ratio = _safe_float(payload.get("mae_overfit_ratio"))
    holdout_ratio = _safe_float(payload.get("holdout_overfit_ratio"))
    sign_gap_val = _safe_float(payload.get("sign_gap_train_val"))
    sign_gap_holdout = _safe_float(payload.get("sign_gap_train_holdout"))

    severe = bool(
        (holdout_ratio is not None and holdout_ratio >= 1.30)
        or (mae_ratio is not None and mae_ratio >= 1.20)
        or (sign_gap_holdout is not None and sign_gap_holdout >= 0.12)
    )
    moderate = bool(
        (holdout_ratio is not None and holdout_ratio >= 1.20)
        or (mae_ratio is not None and mae_ratio >= 1.10)
        or (sign_gap_holdout is not None and sign_gap_holdout >= 0.07)
    )
    mild = bool(
        (holdout_ratio is not None and holdout_ratio >= 1.08)
        or (mae_ratio is not None and mae_ratio >= 1.03)
        or (sign_gap_val is not None and sign_gap_val >= 0.03)
    )

    reasons: list[str] = []
    if holdout_ratio is not None and holdout_ratio >= 1.30:
        reasons.append("holdout_overfit_ratio_ge_1_30")
    elif holdout_ratio is not None and holdout_ratio >= 1.20:
        reasons.append("holdout_overfit_ratio_ge_1_20")
    elif holdout_ratio is not None and holdout_ratio >= 1.08:
        reasons.append("holdout_overfit_ratio_ge_1_08")

    if mae_ratio is not None and mae_ratio >= 1.20:
        reasons.append("mae_overfit_ratio_ge_1_20")
    elif mae_ratio is not None and mae_ratio >= 1.10:
        reasons.append("mae_overfit_ratio_ge_1_10")
    elif mae_ratio is not None and mae_ratio >= 1.03:
        reasons.append("mae_overfit_ratio_ge_1_03")

    if sign_gap_holdout is not None and sign_gap_holdout >= 0.12:
        reasons.append("sign_gap_train_holdout_ge_0_12")
    elif sign_gap_holdout is not None and sign_gap_holdout >= 0.07:
        reasons.append("sign_gap_train_holdout_ge_0_07")

    if sign_gap_val is not None and sign_gap_val >= 0.03:
        reasons.append("sign_gap_train_val_ge_0_03")

    if severe:
        return "severe", reasons[0] if reasons else "severe_thresholds_triggered"
    if moderate:
        return "moderate", reasons[0] if reasons else "moderate_thresholds_triggered"
    if mild:
        return "mild", reasons[0] if reasons else "mild_thresholds_triggered"
    return "none", "within_thresholds"


def compute_direct_overfitting_diagnostics(
    *,
    direct_model,
    X_train_full: pd.DataFrame,
    y_train: pd.DataFrame,
    X_val_full: pd.DataFrame,
    y_val: pd.DataFrame,
    holdout_backtest_summary: Dict[str, Any] | None,
) -> Dict[str, Any]:
    train_metrics = _split_direct_metrics(direct_model, X_train_full, y_train)
    val_metrics = _split_direct_metrics(direct_model, X_val_full, y_val)

    holdout_summary = dict(holdout_backtest_summary or {})
    holdout_direct = dict(holdout_summary.get("direct_model", {}) or {})
    holdout_baselines = dict(holdout_summary.get("direct_baselines", {}) or {})
    holdout_persistence = dict(holdout_baselines.get("persistence", {}) or {})

    holdout_mae = _safe_float(holdout_direct.get("MAE"))
    holdout_sign = _safe_float(holdout_direct.get("sign_accuracy"))
    holdout_persistence_mae = _safe_float(holdout_persistence.get("MAE"))
    holdout_delta_vs_baseline = None
    if holdout_mae is not None and holdout_persistence_mae is not None:
        holdout_delta_vs_baseline = float(holdout_persistence_mae - holdout_mae)

    train_mae = _safe_float(train_metrics.get("MAE"))
    val_mae = _safe_float(val_metrics.get("MAE"))
    train_sign = _safe_float(train_metrics.get("sign_acc"))
    val_sign = _safe_float(val_metrics.get("sign_acc"))

    mae_gap_train_val = None if train_mae is None or val_mae is None else float(val_mae - train_mae)
    mae_gap_train_holdout = None if train_mae is None or holdout_mae is None else float(holdout_mae - train_mae)
    sign_gap_train_val = None if train_sign is None or val_sign is None else float(train_sign - val_sign)
    sign_gap_train_holdout = None if train_sign is None or holdout_sign is None else float(train_sign - holdout_sign)

    mae_overfit_ratio = None
    holdout_overfit_ratio = None
    if train_mae is not None and train_mae > 1e-12:
        if val_mae is not None:
            mae_overfit_ratio = float(val_mae / train_mae)
        if holdout_mae is not None:
            holdout_overfit_ratio = float(holdout_mae / train_mae)

    diagnostics: Dict[str, Any] = {
        "train_MAE": train_mae,
        "val_MAE": val_mae,
        "holdout_MAE": holdout_mae,
        "train_sign_acc": train_sign,
        "train_sign_acc_pct": _accuracy_pct(train_sign),
        "val_sign_acc": val_sign,
        "val_sign_acc_pct": _accuracy_pct(val_sign),
        "holdout_sign_acc": holdout_sign,
        "holdout_sign_acc_pct": _accuracy_pct(holdout_sign),
        "mae_gap_train_val": mae_gap_train_val,
        "mae_gap_train_holdout": mae_gap_train_holdout,
        "sign_gap_train_val": sign_gap_train_val,
        "sign_gap_train_holdout": sign_gap_train_holdout,
        "mae_overfit_ratio": mae_overfit_ratio,
        "holdout_overfit_ratio": holdout_overfit_ratio,
        "train_delta_vs_baseline": _safe_float(train_metrics.get("delta_vs_baseline")),
        "val_delta_vs_baseline": _safe_float(val_metrics.get("delta_vs_baseline")),
        "holdout_delta_vs_baseline": holdout_delta_vs_baseline,
    }

    if train_mae is None or val_mae is None or holdout_mae is None:
        diagnostics["overfit_status"] = "none"
        diagnostics["overfit_reason"] = "insufficient_metrics"
        return diagnostics

    status, reason = _resolve_overfit_status(diagnostics)
    diagnostics["overfit_status"] = status
    diagnostics["overfit_reason"] = reason
    return diagnostics


def overfitting_flat_fields(diagnostics: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(diagnostics or {})
    return {field: payload.get(field) for field in OVERFIT_FIELDS}
