from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
import requests


BYBIT_RATE_LIMIT_CODE = 10006
BYBIT_MAX_RETRIES = 6
BYBIT_RETRY_BASE_SECONDS = 1.0


@dataclass
class DataConfig:
    base_url: str = "https://api.bybit.com"
    interval: str = "60"
    bars: int = 3000
    target_col: str = "close"
    date_col: str = "timestamp"
    test_ratio: float = 0.2


def _interval_to_milliseconds(interval: str) -> int:
    interval_str = str(interval).strip().upper()
    if interval_str.isdigit():
        return int(interval_str) * 60 * 1000

    interval_map = {
        "D": 24 * 60 * 60 * 1000,
        "W": 7 * 24 * 60 * 60 * 1000,
        "M": 30 * 24 * 60 * 60 * 1000,
    }
    if interval_str not in interval_map:
        raise ValueError(f"Unsupported interval value: {interval}")
    return interval_map[interval_str]


def _ensure_market_data_db(db_path: str | Path) -> Path:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_file) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_klines (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                turnover REAL NOT NULL,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, interval, start_ms)
            );

            CREATE INDEX IF NOT EXISTS idx_market_klines_symbol_interval_ts
            ON market_klines(symbol, interval, start_ms DESC);

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                snapshot_ts TEXT NOT NULL,
                last_price REAL,
                bid_price REAL,
                ask_price REAL,
                bid_size REAL,
                ask_size REAL,
                spread REAL,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_ts
            ON market_snapshots(symbol, snapshot_ts DESC);
            """
        )

    return db_file


def _bybit_request_json(
    url: str,
    params: dict[str, Any],
    timeout: int = 30,
    max_retries: int = BYBIT_MAX_RETRIES,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt_idx in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()

            if payload.get("retCode") == 0:
                return payload

            if payload.get("retCode") == BYBIT_RATE_LIMIT_CODE and attempt_idx < max_retries - 1:
                sleep_sec = BYBIT_RETRY_BASE_SECONDS * (2 ** attempt_idx)
                time.sleep(sleep_sec)
                continue

            raise RuntimeError(f"Bybit API error: {payload}")
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt_idx >= max_retries - 1:
                break
            sleep_sec = BYBIT_RETRY_BASE_SECONDS * (2 ** attempt_idx)
            time.sleep(sleep_sec)

    raise RuntimeError(f"Bybit request failed after {max_retries} attempts: {last_error}") from last_error


def save_klines_to_sqlite(df: pd.DataFrame, symbol: str, interval: str, db_path: str | Path) -> int:
    db_file = _ensure_market_data_db(db_path)
    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce", utc=True)
    work = work.dropna(subset=["timestamp"]).copy()

    if "start_ms" not in work.columns:
        work["start_ms"] = work["timestamp"].map(lambda ts: int(pd.Timestamp(ts).timestamp() * 1000)).astype(np.int64)

    records = []
    for row in work.itertuples(index=False):
        records.append(
            (
                symbol,
                str(interval),
                int(getattr(row, "start_ms")),
                pd.Timestamp(getattr(row, "timestamp")).isoformat(),
                float(getattr(row, "open")),
                float(getattr(row, "high")),
                float(getattr(row, "low")),
                float(getattr(row, "close")),
                float(getattr(row, "volume")),
                float(getattr(row, "turnover")),
            )
        )

    with sqlite3.connect(db_file) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO market_klines(
                symbol, interval, start_ms, timestamp, open, high, low, close, volume, turnover
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    return int(len(records))


def load_klines_from_sqlite(
    symbol: str,
    interval: str,
    db_path: str | Path,
    limit: int | None = None,
) -> pd.DataFrame:
    db_file = _ensure_market_data_db(db_path)
    query = """
        SELECT start_ms, timestamp, open, high, low, close, volume, turnover
        FROM market_klines
        WHERE symbol = ? AND interval = ?
        ORDER BY start_ms DESC
    """
    params: list[Any] = [symbol, str(interval)]
    if limit is not None:
        query += " LIMIT ?"
        params.append(int(limit))

    with sqlite3.connect(db_file) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    if len(df) == 0:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    numeric_cols = ["start_ms", "open", "high", "low", "close", "volume", "turnover"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna().sort_values("timestamp").reset_index(drop=True)
    return df[["start_ms", "timestamp", "open", "high", "low", "close", "volume", "turnover"]]


def _get_klines_sqlite_bounds(symbol: str, interval: str, db_path: str | Path) -> dict[str, int | None]:
    db_file = _ensure_market_data_db(db_path)
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS row_count, MIN(start_ms) AS min_start_ms, MAX(start_ms) AS max_start_ms
            FROM market_klines
            WHERE symbol = ? AND interval = ?
            """,
            (symbol, str(interval)),
        ).fetchone()

    row_count, min_start_ms, max_start_ms = row
    return {
        "row_count": int(row_count or 0),
        "min_start_ms": int(min_start_ms) if min_start_ms is not None else None,
        "max_start_ms": int(max_start_ms) if max_start_ms is not None else None,
    }


def fetch_market_snapshot(symbol: str, base_url: str = "https://api.bybit.com") -> dict[str, Any]:
    ticker_endpoint = f"{base_url}/v5/market/tickers"
    orderbook_endpoint = f"{base_url}/v5/market/orderbook"

    ticker_payload = _bybit_request_json(
        ticker_endpoint,
        params={"category": "linear", "symbol": symbol},
    )

    ticker_list = ticker_payload.get("result", {}).get("list", [])
    if not ticker_list:
        raise RuntimeError(f"Bybit ticker returned empty result for {symbol}")
    ticker_row = ticker_list[0]

    orderbook_payload = _bybit_request_json(
        orderbook_endpoint,
        params={"category": "linear", "symbol": symbol, "limit": 1},
    )

    result = orderbook_payload.get("result", {})
    bids = result.get("b", [])
    asks = result.get("a", [])
    best_bid = bids[0] if bids else [np.nan, np.nan]
    best_ask = asks[0] if asks else [np.nan, np.nan]

    snapshot_ms = orderbook_payload.get("time") or ticker_payload.get("time") or int(time.time() * 1000)
    bid_price = float(best_bid[0]) if len(best_bid) > 0 else np.nan
    ask_price = float(best_ask[0]) if len(best_ask) > 0 else np.nan
    return {
        "symbol": symbol,
        "snapshot_ts": pd.to_datetime(int(snapshot_ms), unit="ms", utc=True),
        "last_price": float(ticker_row.get("lastPrice") or np.nan),
        "bid_price": bid_price,
        "ask_price": ask_price,
        "bid_size": float(best_bid[1]) if len(best_bid) > 1 else np.nan,
        "ask_size": float(best_ask[1]) if len(best_ask) > 1 else np.nan,
        "spread": float(ask_price - bid_price) if np.isfinite(bid_price) and np.isfinite(ask_price) else np.nan,
    }


def save_market_snapshot_to_sqlite(snapshot: dict[str, Any], db_path: str | Path) -> None:
    db_file = _ensure_market_data_db(db_path)
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots(
                symbol, snapshot_ts, last_price, bid_price, ask_price, bid_size, ask_size, spread
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(snapshot["symbol"]),
                pd.Timestamp(snapshot["snapshot_ts"]).isoformat(),
                float(snapshot.get("last_price", np.nan)),
                float(snapshot.get("bid_price", np.nan)),
                float(snapshot.get("ask_price", np.nan)),
                float(snapshot.get("bid_size", np.nan)),
                float(snapshot.get("ask_size", np.nan)),
                float(snapshot.get("spread", np.nan)),
            ),
        )


def load_market_snapshots_from_sqlite(symbol: str, db_path: str | Path, limit: int = 10) -> pd.DataFrame:
    db_file = _ensure_market_data_db(db_path)
    with sqlite3.connect(db_file) as conn:
        df = pd.read_sql_query(
            """
            SELECT symbol, snapshot_ts, last_price, bid_price, ask_price, bid_size, ask_size, spread
            FROM market_snapshots
            WHERE symbol = ?
            ORDER BY snapshot_ts DESC
            LIMIT ?
            """,
            conn,
            params=[symbol, int(limit)],
        )

    if len(df) == 0:
        return df

    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce", utc=True)
    numeric_cols = ["last_price", "bid_price", "ask_price", "bid_size", "ask_size", "spread"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def sync_market_data_to_sqlite(
    symbol: str,
    config: DataConfig,
    db_path: str | Path,
    history_bars: int | None = None,
    fetch_snapshot: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    sync_config = DataConfig(
        base_url=config.base_url,
        interval=str(config.interval),
        bars=int(history_bars if history_bars is not None else config.bars),
        target_col=config.target_col,
        date_col=config.date_col,
        test_ratio=config.test_ratio,
    )

    db_file = _ensure_market_data_db(db_path)
    interval_ms = _interval_to_milliseconds(sync_config.interval)
    desired_bars = int(sync_config.bars)
    cache_info = _get_klines_sqlite_bounds(symbol=symbol, interval=sync_config.interval, db_path=db_file)

    if cache_info["row_count"] == 0:
        history_df = fetch_klines(symbol, sync_config)
        save_klines_to_sqlite(history_df, symbol=symbol, interval=sync_config.interval, db_path=db_file)
    else:
        latest_start_ms = int(cache_info["max_start_ms"] or 0)
        now_ms = int(time.time() * 1000)
        current_closed_bar_ms = now_ms - (now_ms % interval_ms)
        missing_recent_bars = max(0, int(np.ceil((current_closed_bar_ms - latest_start_ms) / interval_ms)))
        recent_fetch_bars = min(desired_bars, missing_recent_bars + 1)

        if recent_fetch_bars > 0:
            recent_config = DataConfig(
                base_url=sync_config.base_url,
                interval=sync_config.interval,
                bars=recent_fetch_bars,
                target_col=sync_config.target_col,
                date_col=sync_config.date_col,
                test_ratio=sync_config.test_ratio,
            )
            recent_df = fetch_klines(symbol, recent_config)
            save_klines_to_sqlite(recent_df, symbol=symbol, interval=sync_config.interval, db_path=db_file)

        cache_info = _get_klines_sqlite_bounds(symbol=symbol, interval=sync_config.interval, db_path=db_file)
        missing_older_bars = max(0, desired_bars - int(cache_info["row_count"] or 0))
        oldest_start_ms = cache_info["min_start_ms"]

        if missing_older_bars > 0 and oldest_start_ms is not None:
            older_config = DataConfig(
                base_url=sync_config.base_url,
                interval=sync_config.interval,
                bars=missing_older_bars,
                target_col=sync_config.target_col,
                date_col=sync_config.date_col,
                test_ratio=sync_config.test_ratio,
            )
            older_df = fetch_klines(symbol, older_config, end_ms=int(oldest_start_ms) - 1)
            save_klines_to_sqlite(older_df, symbol=symbol, interval=sync_config.interval, db_path=db_file)

        history_df = load_klines_from_sqlite(
            symbol=symbol,
            interval=sync_config.interval,
            db_path=db_file,
            limit=desired_bars,
        )

    snapshot = None
    if fetch_snapshot:
        snapshot = fetch_market_snapshot(symbol=symbol, base_url=sync_config.base_url)
        save_market_snapshot_to_sqlite(snapshot, db_path=db_file)

    return history_df, snapshot


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


def fetch_klines(symbol: str, config: DataConfig, end_ms: int | None = None) -> pd.DataFrame:
    endpoint = f"{config.base_url}/v5/market/kline"
    all_rows = []
    current_end_ms = int(end_ms) if end_ms is not None else int(time.time() * 1000)

    while len(all_rows) < config.bars:
        limit = min(1000, config.bars - len(all_rows))
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": config.interval,
            "limit": limit,
            "end": current_end_ms,
        }
        payload = _bybit_request_json(endpoint, params=params)

        batch = payload.get("result", {}).get("list", [])
        if not batch:
            break

        all_rows.extend(batch)
        current_end_ms = int(batch[-1][0]) - 1
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
