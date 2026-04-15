from __future__ import annotations

import numpy as np
import pandas as pd

from catboost_floader.core.utils import get_logger

logger = get_logger("structured_features")


def _safe_div(a, b):
    return a / (b + 1e-8)


def add_structured_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add structured price/momentum/volatility features.

    - multi-horizon returns (5,15,30,60)
    - acceleration (delta of multi-horizon returns)
    - distance to rolling highs/lows and bars-since-extrema
    - breakout distance to previous high
    - wick/body ratios and range-compression signals
    - simple interactions (return x volatility)
    """
    out = df.copy()
    if out.empty or "close" not in out.columns:
        return out

    close = pd.to_numeric(out["close"], errors="coerce")

    horizons = [5, 15, 30, 60]
    for h in horizons:
        out[f"return_{h}"] = close.pct_change(periods=h)

    # acceleration features (delta of multi-horizon returns)
    out["accel_5_15"] = out.get("return_5", 0.0) - out.get("return_15", 0.0)
    out["accel_15_30"] = out.get("return_15", 0.0) - out.get("return_30", 0.0)

    # rolling extrema and distances
    extrema_windows = [6, 12, 24, 36]
    for w in extrema_windows:
        roll_max = close.rolling(w, min_periods=1).max()
        roll_min = close.rolling(w, min_periods=1).min()
        out[f"dist_to_roll_max_{w}"] = _safe_div(roll_max - close, roll_max)
        out[f"dist_to_roll_min_{w}"] = _safe_div(close - roll_min, roll_min)

        # bars since last roll max/min (0 means current bar is extreme)
        out[f"bars_since_max_{w}"] = close.rolling(w, min_periods=1).apply(lambda arr: (len(arr) - 1) - int(np.nanargmax(arr)), raw=True)
        out[f"bars_since_min_{w}"] = close.rolling(w, min_periods=1).apply(lambda arr: (len(arr) - 1) - int(np.nanargmin(arr)), raw=True)

        # breakout distance relative to previous rolling max
        prev_max = roll_max.shift(1)
        out[f"breakout_dist_{w}"] = _safe_div(close - prev_max, prev_max)

    # wick/body derived ratios (guard against missing candle parts)
    if "body_size" in out.columns and "hl_spread" in out.columns:
        out["body_to_range"] = _safe_div(out["body_size"], out["hl_spread"])
    if "upper_wick" in out.columns and "lower_wick" in out.columns and "body_size" in out.columns:
        out["wick_to_body"] = _safe_div(out["upper_wick"] + out["lower_wick"], out["body_size"])

    # volatility expansion/compression signals
    if "volatility_3" in out.columns and "volatility_36" in out.columns:
        out["vol_expansion_3_36"] = _safe_div(out["volatility_3"], out["volatility_36"]).replace([np.inf, -np.inf], np.nan)

    # simple interactions
    for h in [5, 15]:
        if f"return_{h}" in out.columns and "volatility_6" in out.columns:
            out[f"r{h}_x_vol6"] = out[f"return_{h}"] * out["volatility_6"]

    # fill small NaNs to keep model pipelines stable
    num_cols = [c for c in out.columns if c != "timestamp"]
    out[num_cols] = out[num_cols].replace([np.inf, -np.inf], np.nan)
    out[num_cols] = out[num_cols].ffill().bfill().fillna(0.0)
    logger.info(f"Added structured features, new shape={out.shape}")
    return out
