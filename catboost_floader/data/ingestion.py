from __future__ import annotations

import os
import sqlite3
import time
from typing import Callable, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from catboost_floader.core.config import (
    ARTIFACTS_DIR,
    BASE_TIMEFRAME,
    BYBIT_API_URL,
    BYBIT_CATEGORY,
    CACHE_ENABLED,
    CACHE_MAX_AGE_MINUTES,
    DEFAULT_LOOKBACK_DAYS,
    ENABLE_LIVE_ORDERBOOK_FEATURES,
    HTTP_TIMEOUT_SECONDS,
    MARKET_DATASET_CACHE_MINUTES,
    ORDERBOOK_DEPTH_LIMIT,
    OUTPUT_DIR,
    REQUEST_LIMIT,
    REQUEST_SLEEP_SECONDS,
    SQLITE_DB_PATH,
    SYMBOL,
)
from catboost_floader.core.utils import ensure_dirs, get_logger, save_json

logger = get_logger("data_ingestion")


TABLE_SCHEMAS = {
    "base_klines": """
        CREATE TABLE IF NOT EXISTS base_klines (
            timestamp TEXT PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL, volume REAL, turnover REAL
        )
    """,
    "mark_klines": """
        CREATE TABLE IF NOT EXISTS mark_klines (
            timestamp TEXT PRIMARY KEY,
            mark_close REAL
        )
    """,
    "index_klines": """
        CREATE TABLE IF NOT EXISTS index_klines (
            timestamp TEXT PRIMARY KEY,
            index_close REAL
        )
    """,
    "premium_klines": """
        CREATE TABLE IF NOT EXISTS premium_klines (
            timestamp TEXT PRIMARY KEY,
            premium_close REAL
        )
    """,
    "open_interest": """
        CREATE TABLE IF NOT EXISTS open_interest (
            timestamp TEXT PRIMARY KEY,
            open_interest REAL
        )
    """,
    "funding": """
        CREATE TABLE IF NOT EXISTS funding (
            timestamp TEXT PRIMARY KEY,
            funding_rate REAL
        )
    """,
    "cache_meta": """
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """,
}


def _db_conn() -> sqlite3.Connection:
    ensure_dirs([OUTPUT_DIR, ARTIFACTS_DIR])
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    for ddl in TABLE_SCHEMAS.values():
        conn.execute(ddl)
    conn.commit()
    return conn


def _artifact_path(name: str) -> str:
    ensure_dirs([ARTIFACTS_DIR])
    return os.path.join(ARTIFACTS_DIR, name)


def _dataset_cache_fresh(path: str, max_age_minutes: int) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) <= max_age_minutes * 60


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO cache_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM cache_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _get(endpoint: str, params: dict) -> dict:
    url = f"{BYBIT_API_URL}{endpoint}"
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    last_exc = None
    for timeout in (HTTP_TIMEOUT_SECONDS, max(HTTP_TIMEOUT_SECONDS, 40)):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(f"Bybit API error for {endpoint}: {payload}")
            return payload
        except Exception as exc:
            last_exc = exc
            logger.warning(f"Request failed for {endpoint} with timeout={timeout}: {exc}")
            time.sleep(1.0)
    raise last_exc


def fetch_bybit_klines(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, from_ts: Optional[int] = None, limit: int = REQUEST_LIMIT) -> pd.DataFrame:
    params = {"category": BYBIT_CATEGORY, "symbol": symbol, "interval": interval, "limit": limit}
    if from_ts is not None:
        params["start"] = int(from_ts * 1000)
    payload = _get("/v5/market/kline", params)
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _fetch_aux_kline(endpoint: str, price_col: str, symbol: str, interval: str, from_ts: Optional[int] = None, limit: int = REQUEST_LIMIT) -> pd.DataFrame:
    params = {"category": BYBIT_CATEGORY, "symbol": symbol, "interval": interval, "limit": limit}
    if from_ts is not None:
        params["start"] = int(from_ts * 1000)
    payload = _get(endpoint, params)
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame(columns=["timestamp", price_col])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
    df[price_col] = pd.to_numeric(df["close"], errors="coerce")
    return df[["timestamp", price_col]].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_mark_price_klines(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, from_ts: Optional[int] = None, limit: int = REQUEST_LIMIT) -> pd.DataFrame:
    return _fetch_aux_kline("/v5/market/mark-price-kline", "mark_close", symbol, interval, from_ts, limit)


def fetch_index_price_klines(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, from_ts: Optional[int] = None, limit: int = REQUEST_LIMIT) -> pd.DataFrame:
    return _fetch_aux_kline("/v5/market/index-price-kline", "index_close", symbol, interval, from_ts, limit)


def fetch_premium_index_klines(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, from_ts: Optional[int] = None, limit: int = REQUEST_LIMIT) -> pd.DataFrame:
    return _fetch_aux_kline("/v5/market/premium-index-price-kline", "premium_close", symbol, interval, from_ts, limit)


def fetch_open_interest_history(symbol: str = SYMBOL, interval_time: str = "5min", limit: int = 200) -> pd.DataFrame:
    payload = _get("/v5/market/open-interest", {"category": BYBIT_CATEGORY, "symbol": symbol, "intervalTime": interval_time, "limit": limit})
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open_interest"])
    df = pd.DataFrame(rows)
    ts_col = "timestamp" if "timestamp" in df.columns else "startTime"
    val_col = "openInterest" if "openInterest" in df.columns else "open_interest"
    df["timestamp"] = pd.to_datetime(df[ts_col].astype("int64"), unit="ms", utc=True)
    df["open_interest"] = pd.to_numeric(df[val_col], errors="coerce")
    return df[["timestamp", "open_interest"]].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_funding_rate_history(symbol: str = SYMBOL, limit: int = 200) -> pd.DataFrame:
    payload = _get("/v5/market/funding/history", {"category": BYBIT_CATEGORY, "symbol": symbol, "limit": limit})
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame(columns=["timestamp", "funding_rate"])
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["fundingRateTimestamp"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["timestamp", "funding_rate"]].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def fetch_ticker_snapshot(symbol: str = SYMBOL) -> pd.DataFrame:
    payload = _get("/v5/market/tickers", {"category": BYBIT_CATEGORY, "symbol": symbol})
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return pd.DataFrame()
    row = rows[0]
    mapping = {
        "lastPrice": "last_price",
        "markPrice": "mark_price_live",
        "indexPrice": "index_price_live",
        "bid1Price": "bid1_price",
        "ask1Price": "ask1_price",
        "bid1Size": "bid1_size",
        "ask1Size": "ask1_size",
        "openInterest": "open_interest_live",
        "fundingRate": "funding_rate_live",
    }
    out = {new: pd.to_numeric(row.get(old), errors="coerce") for old, new in mapping.items()}
    out["timestamp"] = pd.Timestamp.now(tz="UTC")
    if pd.notna(out.get("bid1_price")) and pd.notna(out.get("ask1_price")):
        out["top_spread"] = float(out["ask1_price"] - out["bid1_price"])
    if pd.notna(out.get("bid1_size")) and pd.notna(out.get("ask1_size")):
        denom = float(out["bid1_size"] + out["ask1_size"] + 1e-8)
        out["top_imbalance"] = float((out["bid1_size"] - out["ask1_size"]) / denom)
    return pd.DataFrame([out])


def fetch_orderbook_snapshot(symbol: str = SYMBOL, limit: int = ORDERBOOK_DEPTH_LIMIT) -> pd.DataFrame:
    if not ENABLE_LIVE_ORDERBOOK_FEATURES:
        return pd.DataFrame()
    payload = _get("/v5/market/orderbook", {"category": BYBIT_CATEGORY, "symbol": symbol, "limit": limit})
    result = payload.get("result", {})
    bids = result.get("b", [])
    asks = result.get("a", [])
    if not bids or not asks:
        return pd.DataFrame()

    def _levels_stats(levels: list[list[str]], side: str) -> dict:
        prices = pd.to_numeric(pd.Series([x[0] for x in levels]), errors="coerce")
        sizes = pd.to_numeric(pd.Series([x[1] for x in levels]), errors="coerce")
        return {
            f"{side}_depth_volume": float(sizes.sum()),
            f"{side}_depth_notional": float((prices * sizes).sum()),
            f"{side}_depth_levels": int(len(levels)),
            f"{side}_vwap": float((prices * sizes).sum() / (sizes.sum() + 1e-8)),
        }

    bid_stats = _levels_stats(bids, "bid")
    ask_stats = _levels_stats(asks, "ask")
    bid_volume = bid_stats["bid_depth_volume"]
    ask_volume = ask_stats["ask_depth_volume"]
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    out = {
        "timestamp": pd.Timestamp.now(tz="UTC"),
        **bid_stats,
        **ask_stats,
        "book_spread": best_ask - best_bid,
        "book_imbalance": (bid_volume - ask_volume) / (bid_volume + ask_volume + 1e-8),
        "microprice": (best_ask * bid_volume + best_bid * ask_volume) / (bid_volume + ask_volume + 1e-8),
    }
    return pd.DataFrame([out])


def _read_table(conn: sqlite3.Connection, table: str, start_ts: Optional[pd.Timestamp] = None, end_ts: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    query = f"SELECT * FROM {table}"
    clauses = []
    params = []
    if start_ts is not None:
        clauses.append("timestamp >= ?")
        params.append(start_ts.isoformat())
    if end_ts is not None:
        clauses.append("timestamp <= ?")
        params.append(end_ts.isoformat())
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY timestamp"
    df = pd.read_sql_query(query, conn, params=params)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df


def _write_table(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    out = df.copy()
    if "timestamp" in out.columns:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    cols = out.columns.tolist()
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, list(out.itertuples(index=False, name=None)))
    conn.commit()


def _existing_timestamps(conn: sqlite3.Connection, table: str, start: pd.Timestamp, end: pd.Timestamp) -> set[pd.Timestamp]:
    rows = conn.execute(
        f"SELECT timestamp FROM {table} WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")),
    ).fetchall()
    return {pd.Timestamp(r[0], tz="UTC") for r in rows}


def _missing_minute_ranges(conn: sqlite3.Connection, table: str, start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    expected = pd.date_range(start=start, end=end, freq="1min", tz="UTC")
    existing = _existing_timestamps(conn, table, start, end)
    missing = [ts for ts in expected if ts not in existing]
    if not missing:
        return []
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    rs = re = missing[0]
    for ts in missing[1:]:
        if ts == re + pd.Timedelta(minutes=1):
            re = ts
        else:
            ranges.append((rs, re))
            rs = re = ts
    ranges.append((rs, re))
    return ranges


def _download_missing_ranges(fetch_fn: Callable, symbol: str, interval: str, ranges: list[tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for start, end in ranges:
        ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        while ts <= end_ts:
            df = fetch_fn(symbol=symbol, interval=interval, from_ts=ts, limit=REQUEST_LIMIT)
            if df.empty:
                break
            df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
            if not df.empty:
                pieces.append(df)
                last_ts = int(df["timestamp"].iloc[-1].timestamp())
            else:
                last_ts = ts + REQUEST_LIMIT * 60
            if last_ts < ts:
                break
            ts = last_ts + 60
            time.sleep(REQUEST_SLEEP_SECONDS)
    return pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()


def _ensure_minute_table(conn: sqlite3.Connection, table: str, fetch_fn: Callable, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
    now = (pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta(minutes=1))
    start = now - pd.Timedelta(days=lookback_days)
    missing = _missing_minute_ranges(conn, table, start, now)
    if missing:
        logger.info(f"{table}: fetching {len(missing)} missing minute range(s)")
        try:
            df_new = _download_missing_ranges(fetch_fn, symbol, interval, missing)
            _write_table(conn, table, df_new)
        except Exception as exc:
            logger.warning(f"{table}: failed to fetch missing ranges, using cached rows only: {exc}")
    return _read_table(conn, table, start, now)


def _refresh_slow_table(conn: sqlite3.Connection, table: str, fetch_fn: Callable[[], pd.DataFrame], refresh_minutes: int = 60) -> pd.DataFrame:
    last_refresh = _get_meta(conn, f"refresh:{table}")
    due = True
    if last_refresh:
        try:
            last_ts = pd.Timestamp(last_refresh, tz="UTC")
            due = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds() > refresh_minutes * 60
        except Exception:
            due = True
    if due:
        try:
            df = fetch_fn()
            _write_table(conn, table, df)
            _set_meta(conn, f"refresh:{table}", pd.Timestamp.now(tz="UTC").isoformat())
        except Exception as exc:
            logger.warning(f"Refresh failed for {table}: {exc}")
    return _read_table(conn, table)


def fetch_and_save_bybit_data(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, lookback_days: int = DEFAULT_LOOKBACK_DAYS, force_refresh: bool = False) -> pd.DataFrame:
    conn = _db_conn()
    try:
        if force_refresh:
            conn.execute("DELETE FROM base_klines")
            conn.commit()
        base = _ensure_minute_table(conn, "base_klines", fetch_bybit_klines, symbol, interval, lookback_days)
        if base.empty:
            raise RuntimeError("No data fetched from Bybit.")
        base.to_csv(_artifact_path(f"{symbol}_{interval}_klines.csv"), index=False)
        return base
    finally:
        conn.close()


def assemble_market_dataset(symbol: str = SYMBOL, interval: str = BASE_TIMEFRAME, lookback_days: int = DEFAULT_LOOKBACK_DAYS, force_refresh: bool = False) -> pd.DataFrame:
    dataset_path = _artifact_path(f"{symbol}_{interval}_market_dataset.csv")
    conn = _db_conn()
    try:
        if CACHE_ENABLED and not force_refresh and _dataset_cache_fresh(dataset_path, MARKET_DATASET_CACHE_MINUTES):
            cached = pd.read_csv(dataset_path)
            cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True, errors="coerce")
            return cached

        if force_refresh:
            for table in ["base_klines", "mark_klines", "index_klines", "premium_klines"]:
                conn.execute(f"DELETE FROM {table}")
            conn.commit()

        try:
            base = _ensure_minute_table(conn, "base_klines", fetch_bybit_klines, symbol, interval, lookback_days)
            mark = _ensure_minute_table(conn, "mark_klines", fetch_mark_price_klines, symbol, interval, lookback_days)
            index = _ensure_minute_table(conn, "index_klines", fetch_index_price_klines, symbol, interval, lookback_days)
            premium = _ensure_minute_table(conn, "premium_klines", fetch_premium_index_klines, symbol, interval, lookback_days)
        except Exception as exc:
            logger.warning(f"Minute-table refresh failed, falling back to cached SQLite data: {exc}")
            now = (pd.Timestamp.now(tz="UTC").floor("min") - pd.Timedelta(minutes=1))
            start = now - pd.Timedelta(days=lookback_days)
            base = _read_table(conn, "base_klines", start, now)
            mark = _read_table(conn, "mark_klines", start, now)
            index = _read_table(conn, "index_klines", start, now)
            premium = _read_table(conn, "premium_klines", start, now)

        oi = _refresh_slow_table(conn, "open_interest", lambda: fetch_open_interest_history(symbol=symbol), refresh_minutes=60)
        funding = _refresh_slow_table(conn, "funding", lambda: fetch_funding_rate_history(symbol=symbol), refresh_minutes=60)

        if base.empty:
            if os.path.exists(dataset_path):
                cached = pd.read_csv(dataset_path)
                cached["timestamp"] = pd.to_datetime(cached["timestamp"], utc=True, errors="coerce")
                return cached
            raise RuntimeError("No market data available from Bybit or cache.")

        df = base.copy().sort_values("timestamp").reset_index(drop=True)
        for extra in [mark, index, premium]:
            if not extra.empty:
                df = df.merge(extra, on="timestamp", how="left")

        for slower in [oi, funding]:
            if not slower.empty:
                slower = slower.sort_values("timestamp")
                df = pd.merge_asof(df.sort_values("timestamp"), slower, on="timestamp", direction="backward")

        for col in ["mark_close", "index_close", "premium_close", "open_interest", "funding_rate"]:
            if col in df.columns:
                # Fill both forward and backward so exported cached artifacts do not
                # retain leading NaNs from slower auxiliary feeds.
                df[col] = pd.to_numeric(df[col], errors="coerce").ffill().bfill()

        df = df.sort_values("timestamp").reset_index(drop=True)
        df.to_csv(dataset_path, index=False)
        return df
    finally:
        conn.close()


def fetch_live_microstructure(symbol: str = SYMBOL) -> dict:
    payload: dict = {}
    try:
        ticker = fetch_ticker_snapshot(symbol)
        if not ticker.empty:
            payload.update(ticker.iloc[0].to_dict())
    except Exception as exc:
        logger.warning(f"Ticker snapshot failed: {exc}")
    try:
        orderbook = fetch_orderbook_snapshot(symbol)
        if not orderbook.empty:
            payload.update(orderbook.iloc[0].to_dict())
    except Exception as exc:
        logger.warning(f"Orderbook snapshot failed: {exc}")
    if payload:
        save_json({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in payload.items()}, _artifact_path(f"{symbol}_latest_microstructure.json"))
    return payload
