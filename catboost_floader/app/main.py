from __future__ import annotations

import os
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from catboost_floader.monitoring.anomaly_cleaning import annotate_anomalies, clean_training_anomalies, persist_anomaly_artifacts
from catboost_floader.evaluation.backtest import build_direct_baselines, run_backtest, split_train_test
from catboost_floader.models.confidence import fit_error_calibrator
from catboost_floader.core.config import BACKTEST_DIR, MODEL_DIR, REPORT_DIR, TRAIN_LOOKBACK_DAYS
from catboost_floader.data.ingestion import assemble_market_dataset
from catboost_floader.data.preprocessing import preprocess_data
from catboost_floader.features.engineering import build_direct_features, build_range_features
from catboost_floader.models.tuning import tune_direct_model, tune_range_high_model, tune_range_low_model
from catboost_floader.app.live import run_live_test
from catboost_floader.models.direct import train_direct_model
from catboost_floader.models.range import train_range_model
from catboost_floader.targets.generation import generate_direct_targets, generate_range_targets
from catboost_floader.core.utils import ensure_dirs, save_json


def _feature_stats(df: pd.DataFrame) -> dict:
    stats = {}
    for col in df.columns:
        if col == "timestamp":
            continue
        ser = df[col]
        # Prefer numeric reductions; coerce when possible, otherwise set sensible defaults
        if is_numeric_dtype(ser.dtype):
            mean_val = float(ser.mean())
            std_val = float(ser.std() + 1e-8)
        else:
            coerced = pd.to_numeric(ser, errors="coerce")
            if coerced.notna().any():
                mean_val = float(coerced.mean())
                std_val = float(coerced.std() + 1e-8)
            else:
                mean_val = 0.0
                std_val = 1.0
        stats[col] = {"mean": mean_val, "std": std_val}
    return stats


def _save_feature_importance(direct_model, range_model, direct_cols: list[str], range_cols: list[str]) -> None:
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
    save_json({"direct": direct_imp, "range_low": low_imp, "range_high": high_imp}, os.path.join(REPORT_DIR, "feature_importance.json"))


def _drop_non_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    drop_cols = ["timestamp"]
    drop_cols += [c for c in out.columns if str(c).startswith("target_")]
    out = out.drop(columns=drop_cols, errors="ignore")

    # Keep only numeric columns to avoid passing categorical/text data into CatBoost
    numeric = out.select_dtypes(include=[np.number])
    return numeric.copy()


def _split_train_val(X: pd.DataFrame, y: pd.DataFrame, val_size: float = 0.15) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    split_idx = max(1, int(len(X) * (1 - val_size)))
    return (
        X.iloc[:split_idx].copy().reset_index(drop=True),
        X.iloc[split_idx:].copy().reset_index(drop=True),
        y.iloc[:split_idx].copy().reset_index(drop=True),
        y.iloc[split_idx:].copy().reset_index(drop=True),
    )


def _sync_branch_pair(
    X_direct: pd.DataFrame,
    y_direct: pd.DataFrame,
    X_range: pd.DataFrame,
    y_range: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if X_direct.empty or X_range.empty:
        return (
            X_direct.iloc[0:0].copy().reset_index(drop=True),
            y_direct.iloc[0:0].copy().reset_index(drop=True),
            X_range.iloc[0:0].copy().reset_index(drop=True),
            y_range.iloc[0:0].copy().reset_index(drop=True),
        )

    common_ts = pd.Index(X_direct["timestamp"]).intersection(pd.Index(X_range["timestamp"]))
    if len(common_ts) == 0:
        return (
            X_direct.iloc[0:0].copy().reset_index(drop=True),
            y_direct.iloc[0:0].copy().reset_index(drop=True),
            X_range.iloc[0:0].copy().reset_index(drop=True),
            y_range.iloc[0:0].copy().reset_index(drop=True),
        )

    left_idx = X_direct.loc[X_direct["timestamp"].isin(common_ts)].sort_values("timestamp").index
    right_idx = X_range.loc[X_range["timestamp"].isin(common_ts)].sort_values("timestamp").index

    X_direct2 = X_direct.loc[left_idx].copy().reset_index(drop=True)
    y_direct2 = y_direct.loc[left_idx].copy().reset_index(drop=True)
    X_range2 = X_range.loc[right_idx].copy().reset_index(drop=True)
    y_range2 = y_range.loc[right_idx].copy().reset_index(drop=True)
    return X_direct2, y_direct2, X_range2, y_range2


def _select_direct_strategy(direct_model, X_val_full: pd.DataFrame, y_val: pd.DataFrame) -> Dict[str, object]:
    if X_val_full.empty or y_val.empty:
        return {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}

    X_model = _drop_non_model_columns(X_val_full)
    raw_pred = np.asarray(direct_model.predict(X_model.reindex(columns=direct_model.feature_names, fill_value=0.0)), dtype=float)
    close = pd.to_numeric(X_val_full["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_price = pd.to_numeric(y_val["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    baselines = build_direct_baselines(X_val_full)
    baseline_map = {
        "persistence": pd.to_numeric(baselines["baseline_persistence_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "rolling_mean": pd.to_numeric(baselines["baseline_rolling_mean_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "trend": pd.to_numeric(baselines["baseline_trend_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
    }

    candidates = [{"type": "model_only", "alpha": 1.0, "baseline": "persistence"}]
    alpha_grid = [0.25, 0.4, 0.55, 0.7, 0.85]
    for baseline_name in ["persistence", "rolling_mean"]:
        candidates.append({"type": "baseline_only", "alpha": 0.0, "baseline": baseline_name})
        for alpha in alpha_grid:
            candidates.append({"type": "blend", "alpha": alpha, "baseline": baseline_name})

    best = None
    best_mae = np.inf
    for strategy in candidates:
        if strategy["type"] == "model_only":
            ret = raw_pred
        elif strategy["type"] == "baseline_only":
            ret = baseline_map[strategy["baseline"]]
        else:
            base = baseline_map[strategy["baseline"]]
            ret = strategy["alpha"] * raw_pred + (1.0 - strategy["alpha"]) * base
        pred_price = close * (1.0 + ret)
        mae = float(np.mean(np.abs(target_price - pred_price)))
        if mae < best_mae:
            best_mae = mae
            best = dict(strategy)
            best["validation_mae"] = mae

    return best or {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}


def _calibrate_range_model(range_model, direct_model, X_range_val_full: pd.DataFrame, X_direct_val_full: pd.DataFrame, y_direct_val: pd.DataFrame) -> Dict[str, float]:
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
    scale_grid = [0.15, 0.2, 0.25, 0.33, 0.5, 0.75, 1.0]

    for center_mode, center in [("direct_center", direct_center), ("model_center", model_center)]:
        base_half = np.maximum(model_half, np.abs(model_center - center))
        for scale in scale_grid:
            scaled_half = base_half * scale
            residual = np.maximum(np.abs(actual - center) - scaled_half, 0.0)
            margin_normal = float(residual[normal_mask].max()) if normal_mask.any() else 0.0
            margin_anomaly = float(np.quantile(residual[anomaly_mask], 0.98)) if anomaly_mask.any() else margin_normal
            final_half = scaled_half.copy()
            final_half[normal_mask] += margin_normal
            final_half[anomaly_mask] += margin_anomaly
            avg_norm_width = float(np.mean((2.0 * final_half) / (np.abs(actual) + 1e-8)))
            normal_coverage = float(np.mean(np.abs(actual[normal_mask] - center[normal_mask]) <= final_half[normal_mask])) if normal_mask.any() else 1.0
            if normal_coverage >= 1.0 and avg_norm_width < best_width:
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


def main() -> None:
    ensure_dirs([BACKTEST_DIR, MODEL_DIR, REPORT_DIR])

    print("Loading data...")
    raw = assemble_market_dataset(lookback_days=TRAIN_LOOKBACK_DAYS)

    print("Preprocessing...")
    clean = preprocess_data(raw)

    print("Building branch-specific features...")
    direct_features = build_direct_features(clean)
    range_features = build_range_features(clean)

    print("Generating branch-specific targets...")
    direct_targets = generate_direct_targets(clean)
    range_targets = generate_range_targets(clean)

    print("Aligning direct branch...")
    direct_merged = direct_features.merge(direct_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    print("Aligning range branch...")
    range_merged = range_features.merge(range_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    direct_target_cols = [c for c in direct_merged.columns if str(c).startswith("target_")]
    range_target_cols = [c for c in range_merged.columns if str(c).startswith("target_")]

    X_direct = direct_merged.drop(columns=direct_target_cols, errors="ignore")
    y_direct = direct_merged[["target_future_close", "target_return", "target_log_return"]].copy()
    X_range = range_merged.drop(columns=range_target_cols, errors="ignore")
    y_range = range_merged[["target_range_low", "target_range_high"]].copy()

    print("Synchronizing branches...")
    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    print("Annotating anomalies...")
    X_direct = annotate_anomalies(X_direct)
    X_range = annotate_anomalies(X_range)
    persist_anomaly_artifacts(X_direct)

    print("Cleaning severe anomalies...")
    X_direct, y_direct = clean_training_anomalies(X_direct, y_direct)
    X_range, y_range = clean_training_anomalies(X_range, y_range)

    print("Synchronizing branches after cleaning...")
    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    print("Train/test split...")
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

    feature_stats = _feature_stats(X_direct_fit_model)
    save_json(feature_stats, os.path.join(MODEL_DIR, "feature_stats.json"))

    print("Hyperparameter tuning...")
    direct_params = tune_direct_model(X_direct_fit_model, y_direct_fit["target_return"])
    range_low_params = tune_range_low_model(X_range_fit_model, y_range_fit["target_range_low"])
    range_high_params = tune_range_high_model(X_range_fit_model, y_range_fit["target_range_high"])

    print("Training models...")
    direct_model = train_direct_model(X_direct_fit_model, y_direct_fit, params=direct_params, save=False)
    direct_strategy = _select_direct_strategy(direct_model, X_direct_val, y_direct_val)
    direct_model.strategy = direct_strategy

    range_model = train_range_model(X_range_fit_model, y_range_fit, low_params=range_low_params, high_params=range_high_params, save=False)
    range_calibration = _calibrate_range_model(range_model, direct_model, X_range_val, X_direct_val, y_direct_val)
    range_model.calibration = range_calibration

    ensure_dirs([MODEL_DIR])
    direct_model.save(os.path.join(MODEL_DIR, "direct_model"))
    range_model.save(os.path.join(MODEL_DIR, "range_model"))
    _save_feature_importance(direct_model, range_model, list(X_direct_fit_model.columns), list(X_range_fit_model.columns))

    print("Calibrating confidence...")
    train_pred = pd.Series(direct_model.predict(X_direct_fit))
    calibrator = fit_error_calibrator(_drop_non_model_columns(X_direct_fit), train_pred, y_direct_fit["target_return"])

    print("Running backtest...")
    backtest_df, backtest_summary = run_backtest(
        direct_features=X_direct_test.reset_index(drop=True),
        range_features=X_range_test.reset_index(drop=True),
        direct_targets=y_direct_test.reset_index(drop=True),
        range_targets=y_range_test.reset_index(drop=True),
        direct_model=direct_model,
        range_model=range_model,
        error_calibrator=calibrator,
        direct_feature_stats=feature_stats,
    )

    print("Running live test...")
    try:
        live_result = run_live_test(range_model=range_model, direct_model=direct_model, calibrator=calibrator, feature_stats=feature_stats)
    except Exception as exc:
        live_result = {"status": "failed", "error": str(exc)}
        print(f"Live test skipped due to error: {exc}")

    summary = {
        "direct_fit": len(X_direct_fit_model),
        "direct_val": len(X_direct_val),
        "direct_test": len(X_direct_test_model),
        "range_fit": len(X_range_fit_model),
        "range_val": len(X_range_val),
        "range_test": len(X_range_test_model),
        "features_direct": X_direct_fit_model.shape[1],
        "features_range": X_range_fit_model.shape[1],
        "backtest_rows": len(backtest_df),
        "direct_strategy": direct_strategy,
        "range_calibration": range_calibration,
        "backtest_summary": backtest_summary,
        "live": live_result,
    }
    save_json(summary, os.path.join(REPORT_DIR, "pipeline_summary.json"))
    print("Done")


if __name__ == "__main__":
    main()
