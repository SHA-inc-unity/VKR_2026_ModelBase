from __future__ import annotations

import numpy as np
import pandas as pd

from catboost_floader.core.utils import get_logger

logger = get_logger("feature_engineering")


WINDOWS = [3, 6, 12, 24, 36]


def _base_numeric(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col != "timestamp":
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.sort_values("timestamp").reset_index(drop=True)
    return out


def _series_or_nan(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _series_or_zero(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(0.0, index=df.index, dtype="float64")


def _common_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = _base_numeric(df)

    close = _series_or_nan(out, "close")
    open_ = _series_or_nan(out, "open")
    high = _series_or_nan(out, "high")
    low = _series_or_nan(out, "low")
    volume = _series_or_zero(out, "volume")
    turnover = _series_or_zero(out, "turnover")

    return_1 = close.pct_change()
    log_return_1 = np.log(close / close.shift(1))

    feats = {
        "timestamp": out["timestamp"],
        "close": close,
        "volume": volume,
        "turnover": turnover,
        "return_1": return_1,
        "log_return_1": log_return_1,
        "body_size": (close - open_).abs(),
        "upper_wick": high - pd.concat([open_, close], axis=1).max(axis=1),
        "lower_wick": pd.concat([open_, close], axis=1).min(axis=1) - low,
        "hl_spread": high - low,
        "co_spread": close - open_,
    }

    bid1_price = _series_or_nan(out, "bid1_price")
    ask1_price = _series_or_nan(out, "ask1_price")
    bid1_size = _series_or_nan(out, "bid1_size")
    ask1_size = _series_or_nan(out, "ask1_size")

    if bid1_price.notna().any() and ask1_price.notna().any():
        top_spread = ask1_price - bid1_price
        feats["top_spread"] = top_spread
        spread_mean = top_spread.rolling(12).mean()
        spread_std = top_spread.rolling(12).std()
        feats["spread_zscore"] = (top_spread - spread_mean) / (spread_std + 1e-8)
    if bid1_size.notna().any() and ask1_size.notna().any():
        denom = bid1_size + ask1_size + 1e-8
        top_imbalance = (bid1_size - ask1_size) / denom
        feats["top_imbalance"] = top_imbalance
        feats["imbalance_delta_1"] = top_imbalance.diff(1)
        feats["imbalance_delta_5"] = top_imbalance.diff(5)
        feats["microprice"] = (ask1_price * bid1_size + bid1_price * ask1_size) / (bid1_size + ask1_size + 1e-8)
    if "bid_depth_volume" in out.columns and "ask_depth_volume" in out.columns:
        bid_depth = _series_or_zero(out, "bid_depth_volume")
        ask_depth = _series_or_zero(out, "ask_depth_volume")
        feats["depth_bid"] = bid_depth
        feats["depth_ask"] = ask_depth
        feats["depth_imbalance"] = (bid_depth - ask_depth) / (bid_depth + ask_depth + 1e-8)

    mark_close = _series_or_nan(out, "mark_close")
    index_close = _series_or_nan(out, "index_close")
    premium_close = _series_or_nan(out, "premium_close")
    open_interest = _series_or_nan(out, "open_interest")
    funding_rate = _series_or_nan(out, "funding_rate")

    if mark_close.notna().any():
        feats["mark_close_div"] = (close - mark_close) / (close + 1e-8)
    if index_close.notna().any():
        feats["index_close_div"] = (close - index_close) / (close + 1e-8)
    if premium_close.notna().any():
        feats["premium_vs_close"] = premium_close / (close + 1e-8)
    if open_interest.notna().any():
        feats["oi_delta_3"] = open_interest.diff(3)
        feats["oi_delta_12"] = open_interest.diff(12)
        feats["oi_pct_change_12"] = open_interest.pct_change(12)
    if funding_rate.notna().any():
        feats["funding_rate"] = funding_rate
        feats["funding_rate_change"] = funding_rate.diff()

    for w in WINDOWS:
        roll_mean = close.rolling(w).mean()
        roll_std = close.rolling(w).std()
        feats[f"roll_mean_{w}"] = roll_mean
        feats[f"roll_std_{w}"] = roll_std
        feats[f"roll_min_{w}"] = close.rolling(w).min()
        feats[f"roll_max_{w}"] = close.rolling(w).max()
        feats[f"pct_change_{w}"] = close.pct_change(w)
        feats[f"volatility_{w}"] = log_return_1.rolling(w).std() * np.sqrt(w)
        feats[f"zscore_{w}"] = (close - roll_mean) / (roll_std + 1e-8)
        feats[f"ret_mean_{w}"] = feats[f"pct_change_{w}"].rolling(max(2, min(w, 12))).mean()
        feats[f"range_width_{w}"] = feats[f"roll_max_{w}"] - feats[f"roll_min_{w}"]

    vol_mean_12 = volume.rolling(12).mean()
    vol_std_12 = volume.rolling(12).std()
    feats["volume_zscore_12"] = (volume - vol_mean_12) / (vol_std_12 + 1e-8)
    if "volatility_3" in feats and "volatility_36" in feats:
        feats["vol_ratio_3_36"] = feats["volatility_3"] / (feats["volatility_36"] + 1e-8)
    if "volatility_6" in feats and "volatility_36" in feats:
        feats["vol_ratio_6_36"] = feats["volatility_6"] / (feats["volatility_36"] + 1e-8)
    if "range_width_6" in feats and "range_width_36" in feats:
        feats["range_ratio_6_36"] = feats["range_width_6"] / (feats["range_width_36"] + 1e-8)

    common = pd.DataFrame(feats)
    all_nan_cols = [c for c in common.columns if c != "timestamp" and common[c].isna().all()]
    if all_nan_cols:
        common = common.drop(columns=all_nan_cols)
    return common.copy()


def _finalize_features(out: pd.DataFrame, name: str) -> pd.DataFrame:
    out = out.copy()
    numeric_cols = [c for c in out.columns if c != "timestamp"]
    out[numeric_cols] = out[numeric_cols].ffill().bfill()
    out = out.dropna().reset_index(drop=True)
    logger.info(f"Built {name} features with shape: {out.shape}")
    return out


def build_direct_features(df: pd.DataFrame) -> pd.DataFrame:
    common = _common_feature_frame(df)
    preferred = [
        "timestamp", "close", "return_1", "log_return_1", "co_spread", "hl_spread",
        "body_size", "upper_wick", "lower_wick", "pct_change_3", "pct_change_6",
        "pct_change_12", "pct_change_24", "ret_mean_6", "ret_mean_12", "roll_mean_6", "roll_mean_12",
        "roll_std_6", "roll_std_12", "zscore_6", "zscore_12", "volume", "turnover", "volume_zscore_12",
        "top_spread", "spread_zscore", "top_imbalance", "imbalance_delta_1", "imbalance_delta_5",
        "depth_imbalance", "microprice", "mark_close_div", "index_close_div", "premium_vs_close",
        "oi_delta_3", "oi_delta_12", "oi_pct_change_12", "funding_rate", "funding_rate_change",
    ]
    existing = [c for c in preferred if c in common.columns]
    return _finalize_features(common[existing].copy(), "direct")


def build_range_features(df: pd.DataFrame) -> pd.DataFrame:
    common = _common_feature_frame(df)
    preferred = [
        "timestamp", "close", "log_return_1", "hl_spread", "body_size", "volatility_3", "volatility_6",
        "volatility_12", "volatility_24", "volatility_36", "roll_std_6", "roll_std_12", "roll_std_24",
        "roll_std_36", "zscore_6", "zscore_12", "volume_zscore_12", "range_width_12", "range_width_36",
        "range_ratio_6_36", "vol_ratio_3_36", "vol_ratio_6_36", "top_spread", "spread_zscore",
        "top_imbalance", "depth_imbalance", "mark_close_div", "index_close_div", "premium_vs_close",
        "oi_delta_3", "oi_delta_12", "oi_pct_change_12", "funding_rate_change",
    ]
    existing = [c for c in preferred if c in common.columns]
    return _finalize_features(common[existing].copy(), "range")
