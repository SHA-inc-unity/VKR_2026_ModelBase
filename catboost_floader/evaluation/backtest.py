from __future__ import annotations

import os
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from catboost_floader.monitoring.anomaly_cleaning import annotate_anomalies
from catboost_floader.models.confidence import ErrorCalibrator, compute_confidence, compute_ood_score
from catboost_floader.core.config import BACKTEST_DIR, RANGE_BASELINE_ZSCORE, TEST_SIZE, DIRECTION_DEADBAND
from catboost_floader.core.utils import ensure_dirs, get_logger, save_json

logger = get_logger("backtest")


def split_train_test(*dfs: pd.DataFrame, test_size: float = TEST_SIZE):
    split_idx = max(1, int(len(dfs[0]) * (1 - test_size)))
    return tuple((df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()) for df in dfs)


def regression_metrics(y_true, y_pred) -> Dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_true - y_pred) / (np.abs(y_true) + 1e-8))) * 100)
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def sign_accuracy(y_true, y_pred) -> float:
    return float(np.mean(np.sign(y_true) == np.sign(y_pred)))


def build_direct_baselines(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["baseline_persistence_return"] = 0.0
    out["baseline_rolling_mean_return"] = df.get("ret_mean_30", pd.Series(np.zeros(len(df)), index=df.index)).fillna(0.0)
    out["baseline_trend_return"] = df.get("ret_mean_60", pd.Series(np.zeros(len(df)), index=df.index)).fillna(0.0) * 3.0
    return out


def build_range_baselines(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    current_close = pd.to_numeric(df.get("close", pd.Series(np.zeros(len(df)), index=df.index)), errors="coerce").fillna(0.0)
    vol = pd.to_numeric(df.get("volatility_60", pd.Series(np.zeros(len(df)), index=df.index)), errors="coerce").fillna(0.0)
    width = current_close * vol * np.sqrt(180 / 60) * RANGE_BASELINE_ZSCORE
    out["baseline_range_low"] = current_close - width
    out["baseline_range_high"] = current_close + width
    range_width = pd.to_numeric(df.get("range_width_180", pd.Series(np.zeros(len(df)), index=df.index)), errors="coerce").fillna(0.0)
    out["baseline_hist_quantile_low"] = current_close - range_width / 2.0
    out["baseline_hist_quantile_high"] = current_close + range_width / 2.0
    return out


def _range_metrics(actual_price: pd.Series, low: pd.Series, high: pd.Series) -> dict:
    coverage = float(np.mean((actual_price >= low) & (actual_price <= high)))
    width = (high - low).astype(float)
    avg_width = float(width.mean())
    norm_width = float((width / (actual_price.abs() + 1e-8)).mean())
    return {"coverage": coverage, "avg_band_width": avg_width, "normalized_band_width": norm_width}


def _prepare_model_matrix(df: pd.DataFrame) -> pd.DataFrame:
    out = df.drop(columns=["timestamp", "anomaly_type"], errors="ignore").copy()
    for col in list(out.columns):
        dt = str(out[col].dtype)
        if dt == "object" or dt.startswith("string") or "datetime" in dt:
            out = out.drop(columns=[col])
    return out


def run_backtest(direct_features: pd.DataFrame, range_features: pd.DataFrame, direct_targets: pd.DataFrame, range_targets: pd.DataFrame, direct_model, range_model, error_calibrator: ErrorCalibrator, direct_feature_stats: dict) -> Tuple[pd.DataFrame, dict]:
    logger.info("Running backtest")
    ensure_dirs([BACKTEST_DIR])

    direct_eval = annotate_anomalies(direct_features.copy()).reset_index(drop=True)
    range_eval = annotate_anomalies(range_features.copy()).reset_index(drop=True)

    if "close" not in direct_eval.columns:
        if "close_x" in direct_eval.columns:
            direct_eval = direct_eval.rename(columns={"close_x": "close"})
        elif "close_y" in direct_eval.columns:
            direct_eval = direct_eval.rename(columns={"close_y": "close"})
    if "close" not in direct_eval.columns:
        raise ValueError(f"direct_features must include 'close'. Available: {list(direct_eval.columns)}")

    direct_X = _prepare_model_matrix(direct_eval)
    range_X = _prepare_model_matrix(range_eval)

    pred_return = pd.Series(direct_model.predict(direct_X))
    # attempt to extract submodel predictions when available (Direction / Movement)
    try:
        direction_label = direct_model.direction_model.predict_label(direct_X)
    except Exception:
        direction_label = np.full(len(direct_X), np.nan)
    try:
        direction_expectation = direct_model.direction_model.predict_sign_expectation(direct_X)
    except Exception:
        direction_expectation = np.full(len(direct_X), np.nan)
    try:
        movement_magnitude = direct_model.movement_model.predict(direct_X)
    except Exception:
        movement_magnitude = np.full(len(direct_X), np.nan)
    current_close = pd.to_numeric(direct_eval["close"], errors="coerce").reset_index(drop=True)
    pred_price = current_close * (1.0 + pred_return)
    range_pred = range_model.predict(range_X)
    range_low = pd.Series(range_pred[:, 0])
    range_high = pd.Series(range_pred[:, 1])

    pred_abs_error = pd.Series(error_calibrator.predict(direct_X))
    ood_scores = direct_X.apply(lambda row: compute_ood_score(row, direct_feature_stats), axis=1)
    band_width_norm = (range_high - range_low) / (current_close.abs() + 1e-8)
    confidence = [compute_confidence(e, a, o, w) for e, a, o, w in zip(pred_abs_error, direct_eval["anomaly_score"], ood_scores, band_width_norm)]

    direct_base = build_direct_baselines(direct_eval)
    range_base = build_range_baselines(range_eval)
    baseline_persistence_price = current_close * (1 + direct_base["baseline_persistence_return"])
    baseline_rolling_price = current_close * (1 + direct_base["baseline_rolling_mean_return"])

    merged = pd.DataFrame({
        "timestamp": direct_eval["timestamp"].reset_index(drop=True),
        "close": current_close,
        "target_future_close": direct_targets["target_future_close"].reset_index(drop=True),
        "target_return": direct_targets["target_return"].reset_index(drop=True),
        "direct_pred_return": pred_return,
        "direction_pred_label": pd.Series(direction_label).reset_index(drop=True),
        "direction_pred_expectation": pd.Series(direction_expectation).reset_index(drop=True),
        "movement_pred_magnitude": pd.Series(movement_magnitude).reset_index(drop=True),
        "direct_pred_price": pred_price,
        "range_pred_low": range_low,
        "range_pred_high": range_high,
        "target_range_low": range_targets["target_range_low"].reset_index(drop=True),
        "target_range_high": range_targets["target_range_high"].reset_index(drop=True),
        "confidence": confidence,
        "pred_abs_error": pred_abs_error,
        "ood_score": ood_scores,
        "baseline_persistence_price": baseline_persistence_price,
        "baseline_rolling_price": baseline_rolling_price,
        "baseline_range_low": range_base["baseline_range_low"].reset_index(drop=True),
        "baseline_range_high": range_base["baseline_range_high"].reset_index(drop=True),
        "anomaly_flag": direct_eval["anomaly_flag"].reset_index(drop=True),
        "anomaly_score": direct_eval["anomaly_score"].reset_index(drop=True),
        "anomaly_type": direct_eval["anomaly_type"].reset_index(drop=True),
    })

    direct_summary = regression_metrics(merged["target_future_close"], merged["direct_pred_price"])
    direct_summary["return_MAE"] = float(np.mean(np.abs(merged["target_return"] - merged["direct_pred_return"])))
    direct_summary["sign_accuracy"] = sign_accuracy(merged["target_return"], merged["direct_pred_return"])
    direct_summary["corr"] = float(merged[["target_return", "direct_pred_return"]].corr().iloc[0, 1]) if len(merged) > 2 else np.nan

    # Per-model sign accuracy diagnostics
    dead = float(DIRECTION_DEADBAND)
    y_true = merged["target_return"].to_numpy(dtype=float)
    # true labels with deadband
    true_lbl = np.zeros_like(y_true, dtype=int)
    true_lbl[y_true > dead] = 1
    true_lbl[y_true < -dead] = -1

    per_model = {}
    # Direct: label-based accuracy (uses deadband)
    if "direct_pred_return" in merged.columns:
        y_pred = merged["direct_pred_return"].to_numpy(dtype=float)
        pred_lbl = np.zeros_like(y_pred, dtype=int)
        pred_lbl[y_pred > dead] = 1
        pred_lbl[y_pred < -dead] = -1
        per_model["direct"] = {
            "sign": float(np.mean(np.sign(y_true) == np.sign(y_pred))),
            "label": float(np.mean(pred_lbl == true_lbl)),
            "label_counts": {"-1": int((pred_lbl == -1).sum()), "0": int((pred_lbl == 0).sum()), "1": int((pred_lbl == 1).sum())},
        }

    # Direction submodel
    dir_summary = {}
    if "direction_pred_label" in merged.columns:
        dir_lbl = pd.to_numeric(merged["direction_pred_label"], errors="coerce").fillna(0).to_numpy(dtype=int)
        dir_summary["label_accuracy"] = float(np.mean(dir_lbl == true_lbl))
        # confusion
        conf = {}
        for t in (-1, 0, 1):
            for p in (-1, 0, 1):
                conf[f"true_{t}_pred_{p}"] = int(np.sum((true_lbl == t) & (dir_lbl == p)))
        dir_summary["confusion"] = conf
    if "direction_pred_expectation" in merged.columns:
        dir_exp = merged["direction_pred_expectation"].to_numpy(dtype=float)
        dir_summary["expectation_sign_accuracy"] = float(np.mean(np.sign(dir_exp) == np.sign(y_true)))
    if dir_summary:
        per_model["direction"] = dir_summary

    # Movement submodel (magnitude diagnostics)
    mov_summary = {}
    if "movement_pred_magnitude" in merged.columns:
        mov = merged["movement_pred_magnitude"].to_numpy(dtype=float)
        mov_summary["mean_abs_pred"] = float(np.mean(np.abs(mov)))
        mov_summary["mean_abs_target"] = float(np.mean(np.abs(y_true)))
    if mov_summary:
        per_model["movement"] = mov_summary

    # Range center sign diagnostics
    range_ps = {}
    if "range_pred_low" in merged.columns and "range_pred_high" in merged.columns:
        close = merged["close"].to_numpy(dtype=float)
        center_price = (merged["range_pred_low"].to_numpy(dtype=float) + merged["range_pred_high"].to_numpy(dtype=float)) / 2.0
        center_return = (center_price - close) / (np.abs(close) + 1e-8)
        range_ps["center_sign_accuracy_sign"] = float(np.mean(np.sign(center_return) == np.sign(y_true)))
        center_lbl = np.zeros_like(center_return, dtype=int)
        center_lbl[center_return > dead] = 1
        center_lbl[center_return < -dead] = -1
        range_ps["center_sign_accuracy_label"] = float(np.mean(center_lbl == true_lbl))
    if range_ps:
        per_model["range"] = range_ps

    # Baseline sign diagnostics
    baselines_ps = {}
    if "baseline_persistence_price" in merged.columns:
        base_p = merged["baseline_persistence_price"].to_numpy(dtype=float)
        base_p_ret = (base_p - merged["close"].to_numpy(dtype=float)) / (np.abs(merged["close"].to_numpy(dtype=float)) + 1e-8)
        baselines_ps["persistence"] = {
            "sign": float(np.mean(np.sign(base_p_ret) == np.sign(y_true))),
            "label": float(np.mean(((base_p_ret > dead).astype(int) - (base_p_ret < -dead).astype(int)) == true_lbl)),
        }
    if "baseline_rolling_price" in merged.columns:
        base_r = merged["baseline_rolling_price"].to_numpy(dtype=float)
        base_r_ret = (base_r - merged["close"].to_numpy(dtype=float)) / (np.abs(merged["close"].to_numpy(dtype=float)) + 1e-8)
        baselines_ps["rolling_mean"] = {
            "sign": float(np.mean(np.sign(base_r_ret) == np.sign(y_true))),
            "label": float(np.mean(((base_r_ret > dead).astype(int) - (base_r_ret < -dead).astype(int)) == true_lbl)),
        }
    if baselines_ps:
        per_model["baselines"] = baselines_ps

    baseline_summary = {
        "persistence": regression_metrics(merged["target_future_close"], merged["baseline_persistence_price"]),
        "rolling_mean": regression_metrics(merged["target_future_close"], merged["baseline_rolling_price"]),
    }
    range_summary = _range_metrics(merged["target_future_close"], merged["range_pred_low"], merged["range_pred_high"])
    range_baseline_summary = _range_metrics(merged["target_future_close"], merged["baseline_range_low"], merged["baseline_range_high"])

    anomaly_mask = merged["anomaly_flag"] == 1
    normal_mask = ~anomaly_mask
    regime_summary = {
        "normal_rows": int(normal_mask.sum()),
        "anomaly_rows": int(anomaly_mask.sum()),
        "normal_mae": float(np.mean(np.abs(merged.loc[normal_mask, "target_future_close"] - merged.loc[normal_mask, "direct_pred_price"]))) if normal_mask.any() else None,
        "anomaly_mae": float(np.mean(np.abs(merged.loc[anomaly_mask, "target_future_close"] - merged.loc[anomaly_mask, "direct_pred_price"]))) if anomaly_mask.any() else None,
        "normal_range_coverage": float(np.mean((merged.loc[normal_mask, "target_future_close"] >= merged.loc[normal_mask, "range_pred_low"]) & (merged.loc[normal_mask, "target_future_close"] <= merged.loc[normal_mask, "range_pred_high"]))) if normal_mask.any() else None,
        "anomaly_range_coverage": float(np.mean((merged.loc[anomaly_mask, "target_future_close"] >= merged.loc[anomaly_mask, "range_pred_low"]) & (merged.loc[anomaly_mask, "target_future_close"] <= merged.loc[anomaly_mask, "range_pred_high"]))) if anomaly_mask.any() else None,
    }

    summary = {
        "direct_model": direct_summary,
        "direct_baselines": baseline_summary,
        "range_model": range_summary,
        "range_baseline": range_baseline_summary,
        "regime_summary": regime_summary,
        "rows": int(len(merged)),
    }

    # Attach per-model diagnostics to summary (computed earlier)
    summary["per_model_sign_accuracy"] = per_model

    merged.to_csv(os.path.join(BACKTEST_DIR, "backtest_results.csv"), index=False)
    save_json(summary, os.path.join(BACKTEST_DIR, "backtest_summary.json"))
    save_json(baseline_summary, os.path.join(BACKTEST_DIR, "comparison_vs_baselines.json"))
    merged[["timestamp", "close", "target_future_close", "target_return", "direct_pred_return", "direct_pred_price", "baseline_persistence_price", "baseline_rolling_price", "confidence", "anomaly_flag", "anomaly_score", "anomaly_type"]].to_csv(os.path.join(BACKTEST_DIR, "direct_backtest_results.csv"), index=False)
    merged[["timestamp", "close", "target_future_close", "range_pred_low", "range_pred_high", "baseline_range_low", "baseline_range_high", "confidence", "anomaly_flag", "anomaly_score", "anomaly_type"]].to_csv(os.path.join(BACKTEST_DIR, "range_backtest_results.csv"), index=False)
    logger.info(f"Backtest summary: {summary}")
    return merged, summary
