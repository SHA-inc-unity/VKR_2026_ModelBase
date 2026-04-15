from __future__ import annotations

import os
from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import REPORT_DIR
from catboost_floader.core.utils import (
    _drop_non_model_columns,
    _feature_stats,
    _split_train_val,
    _sync_branch_pair,
    ensure_dirs,
    save_json,
)
from catboost_floader.evaluation.backtest import split_train_test
from catboost_floader.models.tuning import tune_direct_model, tune_range_high_model, tune_range_low_model
from catboost_floader.monitoring.anomaly_cleaning import (
    annotate_anomalies,
    clean_training_anomalies,
    persist_anomaly_artifacts,
)


def _save_feature_importance(
    direct_model,
    range_model,
    direct_cols: list[str],
    range_cols: list[str],
    report_dir: str = REPORT_DIR,
) -> None:
    try:
        direct_imp = {}
        try:
            if getattr(direct_model, "movement_model", None) is not None and getattr(direct_model.movement_model, "model", None) is not None:
                direct_imp = dict(zip(direct_cols, map(float, direct_model.movement_model.model.get_feature_importance())))
            elif getattr(direct_model, "model", None) is not None:
                direct_imp = dict(zip(direct_cols, map(float, direct_model.model.get_feature_importance())))
        except Exception:
            direct_imp = {}
    except Exception:
        direct_imp = {}
    try:
        low_imp = dict(zip(range_cols, map(float, range_model.low_model.get_feature_importance()))) if getattr(range_model, "low_model", None) is not None else {}
        high_imp = dict(zip(range_cols, map(float, range_model.high_model.get_feature_importance()))) if getattr(range_model, "high_model", None) is not None else {}
    except Exception:
        low_imp, high_imp = {}, {}
    save_json({"direct": direct_imp, "range_low": low_imp, "range_high": high_imp}, os.path.join(report_dir, "feature_importance.json"))


def _calibrate_range_model(
    range_model,
    direct_model,
    X_range_val_full: pd.DataFrame,
    X_direct_val_full: pd.DataFrame,
    y_direct_val: pd.DataFrame,
) -> Dict[str, float]:
    # Sync branches strictly by timestamp before any numeric operations.
    X_direct_val_full, y_direct_val, X_range_val_full, _dummy = _sync_branch_pair(
        X_direct_val_full,
        y_direct_val,
        X_range_val_full,
        pd.DataFrame({"timestamp": X_range_val_full["timestamp"]}) if not X_range_val_full.empty else pd.DataFrame({"timestamp": []}),
    )
    if X_range_val_full.empty or X_direct_val_full.empty or y_direct_val.empty:
        return {"scale_normal": 1.0, "scale_anomaly": 1.2, "margin_normal": 0.0, "margin_anomaly": 0.0, "center_mode": "direct_center"}

    X_range_model = _drop_non_model_columns(X_range_val_full)
    X_direct_model = _drop_non_model_columns(X_direct_val_full)

    low_raw = np.asarray(range_model.low_model.predict(X_range_model.reindex(columns=range_model.feature_names, fill_value=0.0)), dtype=float)
    high_raw = np.asarray(range_model.high_model.predict(X_range_model.reindex(columns=range_model.feature_names, fill_value=0.0)), dtype=float)
    low_raw, high_raw = np.minimum(low_raw, high_raw), np.maximum(low_raw, high_raw)
    model_center = (low_raw + high_raw) / 2.0
    model_half = np.maximum((high_raw - low_raw) / 2.0, 1e-8)

    direct_pred_return = np.asarray(direct_model.predict(X_direct_model), dtype=float)
    current_close = pd.to_numeric(X_direct_val_full["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    direct_center = current_close * (1.0 + direct_pred_return)

    actual = pd.to_numeric(y_direct_val["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    anomaly_flags = pd.to_numeric(X_direct_val_full.get("anomaly_flag", 0), errors="coerce").fillna(0).to_numpy(dtype=int)
    normal_mask = anomaly_flags == 0
    anomaly_mask = anomaly_flags == 1

    best = None
    best_width = np.inf
    target_normal_coverage = 0.9
    # Allow tighter scaling factors so we can seek narrower bands.
    scale_grid = [0.05, 0.1, 0.15, 0.2, 0.25, 0.33, 0.5]

    for center_mode, center in [("direct_center", direct_center), ("model_center", model_center)]:
        base_half = np.maximum(model_half, np.abs(model_center - center))
        for scale in scale_grid:
            scaled_half = base_half * scale
            residual = np.maximum(np.abs(actual - center) - scaled_half, 0.0)
            # Use a high quantile for margins instead of the absolute max to avoid single-outlier blowups.
            margin_normal = float(np.quantile(residual[normal_mask], target_normal_coverage)) if normal_mask.any() else 0.0
            margin_anomaly = float(np.quantile(residual[anomaly_mask], 0.98)) if anomaly_mask.any() else margin_normal
            final_half = scaled_half.copy()
            final_half[normal_mask] += margin_normal
            final_half[anomaly_mask] += margin_anomaly
            avg_norm_width = float(np.mean((2.0 * final_half) / (np.abs(actual) + 1e-8)))
            normal_coverage = float(
                np.mean(
                    np.abs(actual[normal_mask] - center[normal_mask]) <= final_half[normal_mask]
                )
            ) if normal_mask.any() else 1.0
            if normal_coverage >= target_normal_coverage and avg_norm_width < best_width:
                best_width = avg_norm_width
                best = {
                    "scale_normal": float(scale),
                    "scale_anomaly": float(max(scale, 1.0)),
                    "margin_normal": float(margin_normal),
                    "margin_anomaly": float(max(margin_anomaly, margin_normal)),
                    "center_mode": center_mode,
                    "validation_normal_coverage": normal_coverage,
                    "validation_avg_norm_width": avg_norm_width,
                }

    if best is None:
        residual = np.maximum(np.abs(actual - direct_center) - model_half, 0.0)
        best = {
            "scale_normal": 1.0,
            "scale_anomaly": 1.2,
            "margin_normal": float(residual[normal_mask].max()) if normal_mask.any() else 0.0,
            "margin_anomaly": float(np.quantile(residual[anomaly_mask], 0.98)) if anomaly_mask.any() else 0.0,
            "center_mode": "direct_center",
        }
    return best


def _prepare_pipeline_splits(
    direct_features: pd.DataFrame,
    range_features: pd.DataFrame,
    direct_targets: pd.DataFrame,
    range_targets: pd.DataFrame,
    *,
    persist_anomalies: bool = False,
) -> Dict[str, Any]:
    direct_merged = direct_features.merge(direct_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    range_merged = range_features.merge(range_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    direct_target_cols = [c for c in direct_merged.columns if str(c).startswith("target_")]
    range_target_cols = [c for c in range_merged.columns if str(c).startswith("target_")]

    X_direct = direct_merged.drop(columns=direct_target_cols, errors="ignore")
    y_direct = direct_merged[["target_future_close", "target_return", "target_log_return"]].copy()
    X_range = range_merged.drop(columns=range_target_cols, errors="ignore")
    y_range = range_merged[["target_range_low", "target_range_high"]].copy()

    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    X_direct = annotate_anomalies(X_direct)
    X_range = annotate_anomalies(X_range)
    if persist_anomalies:
        persist_anomaly_artifacts(X_direct)

    X_direct, y_direct = clean_training_anomalies(X_direct, y_direct)
    X_range, y_range = clean_training_anomalies(X_range, y_range)
    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    (X_direct_train, X_direct_test), (y_direct_train, y_direct_test) = split_train_test(X_direct, y_direct)
    (X_range_train, X_range_test), (y_range_train, y_range_test) = split_train_test(X_range, y_range)

    X_direct_train, y_direct_train, X_range_train, y_range_train = _sync_branch_pair(X_direct_train, y_direct_train, X_range_train, y_range_train)
    X_direct_test, y_direct_test, X_range_test, y_range_test = _sync_branch_pair(X_direct_test, y_direct_test, X_range_test, y_range_test)

    X_direct_fit, X_direct_val, y_direct_fit, y_direct_val = _split_train_val(X_direct_train, y_direct_train, val_size=0.15)
    X_range_fit, X_range_val, y_range_fit, y_range_val = _split_train_val(X_range_train, y_range_train, val_size=0.15)

    X_direct_fit, y_direct_fit, X_range_fit, y_range_fit = _sync_branch_pair(X_direct_fit, y_direct_fit, X_range_fit, y_range_fit)
    X_direct_val, y_direct_val, X_range_val, y_range_val = _sync_branch_pair(X_direct_val, y_direct_val, X_range_val, y_range_val)

    X_direct_fit_model = _drop_non_model_columns(X_direct_fit)
    X_direct_test_model = _drop_non_model_columns(X_direct_test)
    X_range_fit_model = _drop_non_model_columns(X_range_fit)
    X_range_test_model = _drop_non_model_columns(X_range_test)

    return {
        "X_direct": X_direct,
        "y_direct": y_direct,
        "X_range": X_range,
        "y_range": y_range,
        "X_direct_train": X_direct_train,
        "y_direct_train": y_direct_train,
        "X_direct_test": X_direct_test,
        "y_direct_test": y_direct_test,
        "X_range_train": X_range_train,
        "y_range_train": y_range_train,
        "X_range_test": X_range_test,
        "y_range_test": y_range_test,
        "X_direct_fit": X_direct_fit,
        "y_direct_fit": y_direct_fit,
        "X_direct_val": X_direct_val,
        "y_direct_val": y_direct_val,
        "X_range_fit": X_range_fit,
        "y_range_fit": y_range_fit,
        "X_range_val": X_range_val,
        "y_range_val": y_range_val,
        "X_direct_fit_model": X_direct_fit_model,
        "X_direct_test_model": X_direct_test_model,
        "X_range_fit_model": X_range_fit_model,
        "X_range_test_model": X_range_test_model,
        "feature_stats": _feature_stats(X_direct_fit_model),
    }


def _tune_pipeline_models(prepared: Dict[str, Any], *, skip_tuning: bool = False) -> Dict[str, object]:
    if skip_tuning:
        return {"direct": None, "range_low": None, "range_high": None}

    return {
        "direct": tune_direct_model(prepared["X_direct_fit_model"], prepared["y_direct_fit"]["target_return"]),
        "range_low": tune_range_low_model(prepared["X_range_fit_model"], prepared["y_range_fit"]["target_range_low"]),
        "range_high": tune_range_high_model(prepared["X_range_fit_model"], prepared["y_range_fit"]["target_range_high"]),
    }


def _export_raw_model_artifacts(backtest_df: pd.DataFrame, output_dir: str) -> None:
    ensure_dirs([output_dir])

    raw_cols = [
        "timestamp",
        "close",
        "target_future_close",
        "target_return",
        "direct_pred_return",
        "direct_pred_price",
        "range_pred_low",
        "range_pred_high",
        "target_range_low",
        "target_range_high",
        "confidence",
        "pred_abs_error",
        "ood_score",
        "anomaly_flag",
        "anomaly_score",
        "anomaly_type",
    ]
    raw_cols = [c for c in raw_cols if c in backtest_df.columns]
    if raw_cols:
        backtest_df[raw_cols].to_csv(os.path.join(output_dir, "raw_predictions.csv"), index=False)

    baseline_cols = [
        "timestamp",
        "close",
        "target_future_close",
        "baseline_persistence_price",
        "baseline_rolling_price",
        "baseline_range_low",
        "baseline_range_high",
    ]
    baseline_cols = [c for c in baseline_cols if c in backtest_df.columns]
    if baseline_cols:
        backtest_df[baseline_cols].to_csv(os.path.join(output_dir, "baseline_outputs.csv"), index=False)

    direction_cols = [
        "timestamp",
        "target_return",
        "direction_pred_label",
        "direction_pred_expectation",
        "direction_proba_neg",
        "direction_proba_zero",
        "direction_proba_pos",
    ]
    direction_cols = [c for c in direction_cols if c in backtest_df.columns]
    if len(direction_cols) > 2:
        backtest_df[direction_cols].to_csv(os.path.join(output_dir, "direction_outputs.csv"), index=False)

    movement_cols = ["timestamp", "target_return", "movement_pred_magnitude", "direct_pred_return"]
    movement_cols = [c for c in movement_cols if c in backtest_df.columns]
    if len(movement_cols) > 2:
        backtest_df[movement_cols].to_csv(os.path.join(output_dir, "movement_outputs.csv"), index=False)
