from __future__ import annotations

import os
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from catboost_floader.core.config import BACKTEST_DIR, MODEL_DIR, REPORT_DIR


def _feature_stats(df: pd.DataFrame) -> dict:
    stats = {}
    for col in df.columns:
        if col == "timestamp":
            continue
        ser = df[col]
        # Prefer numeric reductions; coerce when possible, otherwise set sensible defaults.
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


def _drop_non_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    drop_cols = ["timestamp"]
    drop_cols += [c for c in out.columns if str(c).startswith("target_")]
    out = out.drop(columns=drop_cols, errors="ignore")

    # Keep only numeric columns to avoid passing categorical/text data into CatBoost.
    numeric = out.select_dtypes(include=[np.number])
    return numeric.copy()


def _split_train_val(
    X: pd.DataFrame,
    y: pd.DataFrame,
    val_size: float = 0.15,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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


def _prepare_catboost_params(
    params: Dict[str, Any] | None,
    default_params: Dict[str, Any],
    thread_count: int | None,
) -> Dict[str, Any] | None:
    if params is None and thread_count is None:
        return None
    out = dict(params) if params is not None else dict(default_params)
    out.pop("task_type", None)
    out.pop("devices", None)
    out.pop("gpu_ram_part", None)
    if thread_count is not None:
        out["thread_count"] = max(1, int(thread_count))
    return out


def _multi_model_artifact_paths(key: str) -> Dict[str, str]:
    model_dir = os.path.join(MODEL_DIR, "multi_models", key)
    report_dir = os.path.join(REPORT_DIR, "multi_models", key)
    backtest_dir = os.path.join(BACKTEST_DIR, "multi_models", key)
    return {
        "model_dir": model_dir,
        "report_dir": report_dir,
        "backtest_dir": backtest_dir,
        "direct_model": os.path.join(model_dir, "direct_model.json"),
        "range_model": os.path.join(model_dir, "range_model.json"),
        "feature_stats": os.path.join(model_dir, "feature_stats.json"),
        "feature_importance": os.path.join(report_dir, "feature_importance.json"),
        "backtest_results": os.path.join(backtest_dir, "backtest_results.csv"),
        "raw_predictions": os.path.join(backtest_dir, "raw_predictions.csv"),
        "baseline_outputs": os.path.join(backtest_dir, "baseline_outputs.csv"),
        "comparison_vs_baselines": os.path.join(backtest_dir, "comparison_vs_baselines.json"),
        "multi_window_metrics": os.path.join(backtest_dir, "multi_window_metrics.csv"),
        "multi_window_summary": os.path.join(backtest_dir, "multi_window_summary.json"),
        "direction_outputs": os.path.join(backtest_dir, "direction_outputs.csv"),
        "movement_outputs": os.path.join(backtest_dir, "movement_outputs.csv"),
        "pipeline_metadata": os.path.join(backtest_dir, "pipeline_metadata.json"),
    }
