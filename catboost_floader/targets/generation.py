from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional

from catboost_floader.core.config import DIRECT_HORIZON, MEDIUM_HORIZON, RANGE_QUANTILES, SHORT_HORIZON, WIDE_RANGE_QUANTILES


def generate_direct_targets(df: pd.DataFrame, horizon_steps: Optional[int] = None) -> pd.DataFrame:
    """Generate direct targets for a specified horizon (in rows).

    If `horizon_steps` is None, falls back to global `DIRECT_HORIZON`.
    """
    if df is None or df.empty:
        raise ValueError("Input dataframe is empty.")
    future_steps = int(horizon_steps) if horizon_steps is not None else int(DIRECT_HORIZON)
    base = pd.DataFrame({"timestamp": df["timestamp"].copy()})
    close = pd.to_numeric(df["close"], errors="coerce")
    future_close = close.shift(-future_steps)
    base["target_future_close"] = future_close
    base["target_return"] = future_close / (close + 1e-8) - 1.0
    base["target_log_return"] = np.log(future_close / (close + 1e-8))
    # Auxiliary horizons for diagnostics and future ensembling (kept as counts)
    base["target_return_30m"] = close.shift(-SHORT_HORIZON) / (close + 1e-8) - 1.0
    base["target_return_60m"] = close.shift(-MEDIUM_HORIZON) / (close + 1e-8) - 1.0
    base = base.dropna(subset=["target_future_close", "target_return", "target_log_return"]).reset_index(drop=True)
    return base


def generate_range_targets(df: pd.DataFrame, future_window: Optional[int] = None) -> pd.DataFrame:
    """Generate range targets (low/high) over a rolling future window (in rows).

    If `future_window` is None, falls back to global `DIRECT_HORIZON`.
    """
    if df is None or df.empty:
        raise ValueError("Input dataframe is empty.")
    base = pd.DataFrame({"timestamp": df["timestamp"].copy()})
    close = pd.to_numeric(df["close"], errors="coerce")

    future_window_steps = int(future_window) if future_window is not None else int(DIRECT_HORIZON)
    shifted = close.shift(-future_window_steps + 1)
    base["target_range_low"] = shifted.rolling(window=future_window_steps, min_periods=future_window_steps).quantile(RANGE_QUANTILES[0])
    base["target_range_high"] = shifted.rolling(window=future_window_steps, min_periods=future_window_steps).quantile(RANGE_QUANTILES[1])
    base["target_range_low_wide"] = shifted.rolling(window=future_window_steps, min_periods=future_window_steps).quantile(WIDE_RANGE_QUANTILES[0])
    base["target_range_high_wide"] = shifted.rolling(window=future_window_steps, min_periods=future_window_steps).quantile(WIDE_RANGE_QUANTILES[1])
    base = base.dropna(subset=["target_range_low", "target_range_high"]).reset_index(drop=True)
    return base
