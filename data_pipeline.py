from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import requests


@dataclass
class DataConfig:
    base_url: str = "https://api.bybit.com"
    interval: str = "60"
    bars: int = 3000
    target_col: str = "close"
    date_col: str = "timestamp"
    test_ratio: float = 0.2


class DataProcessor:
    def __init__(self, target_col: str = "close", date_col: str = "timestamp", max_abs_return: float = 0.25, mad_threshold: float = 8.0):
        self.target_col = target_col
        self.date_col = date_col
        self.max_abs_return = max_abs_return
        self.mad_threshold = mad_threshold

    def process(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
        report = {"initial_rows": int(len(df))}
        out = df.copy()

        out[self.date_col] = pd.to_datetime(out[self.date_col], errors="coerce", utc=True)
        out = out.dropna(subset=[self.date_col]).sort_values(self.date_col).drop_duplicates(subset=[self.date_col], keep="last")

        out[self.target_col] = pd.to_numeric(out[self.target_col], errors="coerce")
        out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=[self.target_col])

        before_positive = len(out)
        out = out[out[self.target_col] > 0].copy()
        report["removed_nonpositive_or_zero"] = int(before_positive - len(out))

        out["_log_ret"] = np.log(out[self.target_col]).diff()
        ret = out["_log_ret"].dropna()

        removed_outliers = 0
        if len(ret) > 10:
            med = float(ret.median())
            mad = float(np.median(np.abs(ret - med))) + 1e-9
            modified_z = 0.6745 * (out["_log_ret"] - med) / mad
            mask_extreme = out["_log_ret"].abs() > self.max_abs_return
            mask_mad = modified_z.abs() > self.mad_threshold
            outlier_mask = (mask_extreme | mask_mad).fillna(False)
            outlier_mask.iloc[0] = False
            removed_outliers = int(outlier_mask.sum())
            out = out.loc[~outlier_mask].copy()

        out = out.drop(columns=["_log_ret"], errors="ignore").reset_index(drop=True)

        report["removed_outliers"] = removed_outliers
        report["final_rows"] = int(len(out))
        report["removed_total"] = int(report["initial_rows"] - report["final_rows"])
        return out, report


def fetch_klines(symbol: str, config: DataConfig) -> pd.DataFrame:
    endpoint = f"{config.base_url}/v5/market/kline"
    all_rows = []
    end_ms = int(time.time() * 1000)

    while len(all_rows) < config.bars:
        limit = min(1000, config.bars - len(all_rows))
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": config.interval,
            "limit": limit,
            "end": end_ms,
        }
        response = requests.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error {symbol}: {payload}")

        batch = payload.get("result", {}).get("list", [])
        if not batch:
            break

        all_rows.extend(batch)
        end_ms = int(batch[-1][0]) - 1
        time.sleep(0.08)

    if not all_rows:
        raise RuntimeError(f"Нет данных для {symbol}")

    cols = ["start_ms", "open", "high", "low", "close", "volume", "turnover"]
    df = pd.DataFrame(all_rows, columns=cols).drop_duplicates(subset=["start_ms"])
    df["start_ms"] = pd.to_numeric(df["start_ms"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna().copy()
    df["timestamp"] = pd.to_datetime(df["start_ms"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    if len(df) > config.bars:
        df = df.iloc[-config.bars :].reset_index(drop=True)

    return df[["timestamp", "open", "high", "low", "close", "volume", "turnover"]]


def split_series(series: pd.Series, test_ratio: float) -> Tuple[pd.Series, pd.Series]:
    split_idx = int(len(series) * (1.0 - test_ratio))
    split_idx = max(1, min(split_idx, len(series) - 1))
    train = series.iloc[:split_idx].reset_index(drop=True)
    test = series.iloc[split_idx:].reset_index(drop=True)
    return train, test


def build_datasets(cleaned_data: Dict[str, pd.DataFrame], target_col: str, test_ratio: float) -> Dict[str, Dict[str, pd.Series]]:
    datasets = {}
    for symbol, df in cleaned_data.items():
        full = df[target_col].astype(float).reset_index(drop=True)
        train, test = split_series(full, test_ratio)
        datasets[symbol] = {"full": full, "train": train, "test": test}
    return datasets
