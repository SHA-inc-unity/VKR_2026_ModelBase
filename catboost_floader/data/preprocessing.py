from __future__ import annotations

import pandas as pd

from catboost_floader.core.config import MODEL_TIMEFRAME_MINUTES
from catboost_floader.core.utils import get_logger

logger = get_logger("data_preprocessing")

REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "turnover"]


def aggregate_for_modeling(df: pd.DataFrame, timeframe_min: int) -> pd.DataFrame:
    """Aggregate minute-level dataframe into `timeframe_min`-minute bars.

    Public helper (previously _aggregate_for_modeling). Returns a dataframe
    resampled by the requested minute rule.
    """
    if timeframe_min <= 1:
        return df.copy()

    out = df.copy().set_index("timestamp")
    rule = f"{timeframe_min}min"
    agg_map: dict[str, str] = {}
    for col in out.columns:
        if col == "open":
            agg_map[col] = "first"
        elif col == "high":
            agg_map[col] = "max"
        elif col == "low":
            agg_map[col] = "min"
        elif col == "close":
            agg_map[col] = "last"
        elif col in {"volume", "turnover"}:
            agg_map[col] = "sum"
        else:
            agg_map[col] = "last"

    out = out.resample(rule, label="right", closed="right").agg(agg_map)
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


# Backwards-compatible alias for code still referencing the old private name
_aggregate_for_modeling = aggregate_for_modeling


def preprocess_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Input dataframe is empty.")

    missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    numeric_columns = [c for c in out.columns if c != "timestamp"]
    for col in numeric_columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    if out["timestamp"].isna().any():
        out = out.dropna(subset=["timestamp"]).reset_index(drop=True)

    out = out.ffill().bfill()
    out = out.dropna().reset_index(drop=True)
    out = aggregate_for_modeling(out, MODEL_TIMEFRAME_MINUTES)

    logger.info(f"Preprocessed dataframe shape: {out.shape}")
    return out
