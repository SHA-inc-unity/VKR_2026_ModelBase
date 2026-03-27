from __future__ import annotations

import numpy as np
import pandas as pd

from catboost_floader.core.config import DIRECT_HORIZON, MEDIUM_HORIZON, RANGE_QUANTILES, SHORT_HORIZON, WIDE_RANGE_QUANTILES


def generate_direct_targets(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Input dataframe is empty.")
    base = pd.DataFrame({"timestamp": df["timestamp"].copy()})
    close = pd.to_numeric(df["close"], errors="coerce")
    future_close = close.shift(-DIRECT_HORIZON)
    base["target_future_close"] = future_close
    base["target_return"] = future_close / (close + 1e-8) - 1.0
    base["target_log_return"] = np.log(future_close / (close + 1e-8))
    # Auxiliary horizons for diagnostics and future ensembling.
    base["target_return_30m"] = close.shift(-SHORT_HORIZON) / (close + 1e-8) - 1.0
    base["target_return_60m"] = close.shift(-MEDIUM_HORIZON) / (close + 1e-8) - 1.0
    base = base.dropna(subset=["target_future_close", "target_return", "target_log_return"]).reset_index(drop=True)
    return base


def generate_range_targets(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Input dataframe is empty.")
    base = pd.DataFrame({"timestamp": df["timestamp"].copy()})
    close = pd.to_numeric(df["close"], errors="coerce")

    future_window = DIRECT_HORIZON
    shifted = close.shift(-future_window + 1)
    base["target_range_low"] = shifted.rolling(window=future_window, min_periods=future_window).quantile(RANGE_QUANTILES[0])
    base["target_range_high"] = shifted.rolling(window=future_window, min_periods=future_window).quantile(RANGE_QUANTILES[1])
    base["target_range_low_wide"] = shifted.rolling(window=future_window, min_periods=future_window).quantile(WIDE_RANGE_QUANTILES[0])
    base["target_range_high_wide"] = shifted.rolling(window=future_window, min_periods=future_window).quantile(WIDE_RANGE_QUANTILES[1])
    base = base.dropna(subset=["target_range_low", "target_range_high"]).reset_index(drop=True)
    return base
