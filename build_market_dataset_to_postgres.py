#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from urllib import error, parse, request

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import execute_values
except ImportError as exc:
    raise SystemExit(
        "Missing dependency 'psycopg2'. Install it with: pip install psycopg2-binary"
    ) from exc


BYBIT_BASE_URL = "https://api.bybit.com"
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "crypt_date"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 4
PAGE_LIMIT_KLINE = 1000
PAGE_LIMIT_FUNDING = 200
PAGE_LIMIT_OPEN_INTEREST = 200
UPSERT_BATCH_SIZE = 1000
ORDERBOOK_FIELDS = ("bid1_price", "ask1_price", "bid1_size", "ask1_size")

TIMEFRAMES = {
    "1m": ("1", 60_000),
    "3m": ("3", 180_000),
    "5m": ("5", 300_000),
    "15m": ("15", 900_000),
    "30m": ("30", 1_800_000),
    "60m": ("60", 3_600_000),
    "120m": ("120", 7_200_000),
    "240m": ("240", 14_400_000),
    "360m": ("360", 21_600_000),
    "720m": ("720", 43_200_000),
    "1d": ("D", 86_400_000),
}

TIMEFRAME_ALIASES = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "60m",
    "1h": "60m",
    "120": "120m",
    "2h": "120m",
    "240": "240m",
    "4h": "240m",
    "360": "360m",
    "6h": "360m",
    "720": "720m",
    "12h": "720m",
    "d": "1d",
}

OPEN_INTEREST_INTERVALS = [
    ("5min", 300_000),
    ("15min", 900_000),
    ("30min", 1_800_000),
    ("1h", 3_600_000),
    ("4h", 14_400_000),
    ("1d", 86_400_000),
]


def log(message: str) -> None:
    """Печатает короткое сообщение в консоль."""
    print(message, flush=True)


def normalize_timeframe(value: str) -> tuple[str, str, int]:
    """Нормализует таймфрейм для Bybit и имени таблицы."""
    key = value.strip().lower()
    key = TIMEFRAME_ALIASES.get(key, key)
    if key not in TIMEFRAMES:
        supported = ", ".join(sorted(TIMEFRAMES))
        raise ValueError(f"Unsupported timeframe '{value}'. Supported values: {supported}")
    bybit_interval, step_ms = TIMEFRAMES[key]
    return key, bybit_interval, step_ms


def parse_timestamp_to_ms(value: str) -> int:
    """Преобразует время в миллисекунды UTC."""
    value = value.strip()
    if not value:
        raise ValueError("Timestamp value cannot be empty")
    if value.isdigit():
        number = int(value)
        return number if number >= 1_000_000_000_000 else number * 1000
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp() * 1000)


def ms_to_datetime(value_ms: int) -> datetime:
    """Переводит миллисекунды в datetime UTC."""
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def floor_to_step(value_ms: int, step_ms: int) -> int:
    """Округляет время вниз до границы свечи."""
    return (value_ms // step_ms) * step_ms


def ceil_to_step(value_ms: int, step_ms: int) -> int:
    """Округляет время вверх до ближайшей свечи."""
    return ((value_ms + step_ms - 1) // step_ms) * step_ms


def normalize_window(start_ms: int, end_ms: int, step_ms: int) -> tuple[int, int]:
    """Оставляет только закрытые свечи в заданном диапазоне."""
    if start_ms >= end_ms:
        raise ValueError("Start timestamp must be earlier than end timestamp")
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    end_ms = min(end_ms, now_ms - step_ms)
    start_ms = ceil_to_step(start_ms, step_ms)
    end_ms = floor_to_step(end_ms, step_ms)
    if start_ms > end_ms:
        raise RuntimeError("No closed candles in the requested window")
    return start_ms, end_ms


def make_table_name(symbol: str, timeframe: str) -> str:
    """Строит имя таблицы вида <symbol>_<timeframe>."""
    return f"{symbol.lower()}_{timeframe.lower()}"


def choose_open_interest_interval(step_ms: int) -> tuple[str, int]:
    """Выбирает ближайший интервал open interest."""
    selected = OPEN_INTEREST_INTERVALS[0]
    for label, interval_ms in OPEN_INTEREST_INTERVALS:
        if interval_ms <= step_ms:
            selected = (label, interval_ms)
    return selected


def api_get_json(path: str, params: dict[str, object]) -> dict:
    """Запрашивает JSON у публичного API Bybit с повторами."""
    url = f"{BYBIT_BASE_URL}{path}?{parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    headers = {"Accept": "application/json", "User-Agent": "market-dataset-demo/1.0"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"Failed to fetch {path}: {exc}") from exc

        if payload.get("retCode") == 0:
            return payload
        if attempt < MAX_RETRIES and payload.get("retCode") in {10000, 10006, 10016, 10018}:
            time.sleep(attempt)
            continue
        raise RuntimeError(
            f"Bybit API error for {path}: retCode={payload.get('retCode')}, retMsg={payload.get('retMsg')}"
        )
    raise RuntimeError(f"Exhausted retries for {path}")


def fetch_instrument_details(category: str, symbol: str) -> tuple[int, int]:
    """Получает launchTime и fundingInterval по инструменту."""
    payload = api_get_json(
        "/v5/market/instruments-info",
        {"category": category, "symbol": symbol, "limit": 1},
    )
    items = payload.get("result", {}).get("list", [])
    if not items:
        raise RuntimeError(f"Instrument not found for {category}:{symbol}")
    item = items[0]
    launch_time_ms = int(item.get("launchTime", "0") or 0)
    funding_interval_ms = int(item.get("fundingInterval", 0) or 0) * 60_000
    return launch_time_ms, funding_interval_ms or 28_800_000


def fetch_index_prices(
    category: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    progress_callback=None,
    progress_start_ms: int | None = None,
    progress_end_ms: int | None = None,
) -> list[tuple[int, float]]:
    """Скачивает исторические index_price свечи и сообщает частичный прогресс."""
    rows = {}
    current_end = end_ms
    callback_start = start_ms if progress_start_ms is None else progress_start_ms
    callback_end = end_ms if progress_end_ms is None else progress_end_ms
    while current_end >= start_ms:
        payload = api_get_json(
            "/v5/market/index-price-kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "start": start_ms,
                "end": current_end,
                "limit": PAGE_LIMIT_KLINE,
            },
        )
        items = payload.get("result", {}).get("list", [])
        if not items:
            break
        oldest = None
        for item in items:
            timestamp_ms = int(item[0])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item[4])
                oldest = timestamp_ms if oldest is None else min(oldest, timestamp_ms)
        if progress_callback is not None:
            progress_callback(sum(1 for timestamp in rows if callback_start <= timestamp <= callback_end))
        if oldest is None or oldest <= start_ms:
            break
        current_end = oldest - 1
    return sorted(rows.items())


def fetch_funding_rates(category: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические funding rate точки."""
    rows = {}
    current_end = end_ms
    while current_end >= start_ms:
        payload = api_get_json(
            "/v5/market/funding/history",
            {
                "category": category,
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": current_end,
                "limit": PAGE_LIMIT_FUNDING,
            },
        )
        items = payload.get("result", {}).get("list", [])
        if not items:
            break
        oldest = None
        for item in items:
            timestamp_ms = int(item["fundingRateTimestamp"])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item["fundingRate"])
                oldest = timestamp_ms if oldest is None else min(oldest, timestamp_ms)
        if oldest is None or oldest <= start_ms:
            break
        current_end = oldest - 1
    return sorted(rows.items())


def fetch_open_interest(category: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические точки open interest."""
    rows = {}
    cursor = None
    seen = set()
    while True:
        payload = api_get_json(
            "/v5/market/open-interest",
            {
                "category": category,
                "symbol": symbol,
                "intervalTime": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": PAGE_LIMIT_OPEN_INTEREST,
                "cursor": cursor,
            },
        )
        result = payload.get("result", {})
        items = result.get("list", [])
        if not items:
            break
        for item in items:
            timestamp_ms = int(item["timestamp"])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item["openInterest"])
        cursor = result.get("nextPageCursor") or None
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return sorted(rows.items())


def align_asof(series: list[tuple[int, float]], timestamps: list[int]) -> list[float | None]:
    """Выравнивает редкую серию по свечным timestamp."""
    result = []
    index = 0
    last_value = None
    for timestamp in timestamps:
        while index < len(series) and series[index][0] <= timestamp:
            last_value = series[index][1]
            index += 1
        result.append(last_value)
    return result


def compute_rsi(prices: list[float], period: int) -> list[float | None]:
    """Вычисляет RSI по index_price."""
    if period <= 0:
        raise ValueError("RSI period must be positive")
    rsi = [None] * len(prices)
    if len(prices) <= period:
        return rsi
    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rsi[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    if avg_gain == 0 and avg_loss == 0:
        rsi[period] = 50.0
    for i in range(period + 1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_gain == 0 and avg_loss == 0:
            rsi[i] = 50.0
        elif avg_loss == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return rsi


def table_exists(connection: psycopg2.extensions.connection, table_name: str) -> bool:
    """Проверяет наличие таблицы в PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
        return cursor.fetchone()[0] is not None


def ensure_table(connection: psycopg2.extensions.connection, table_name: str) -> None:
    """Создает таблицу для демо-датасета, если ее нет."""
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    timestamp_utc TIMESTAMPTZ PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    index_price DOUBLE PRECISION NOT NULL,
                    funding_rate DOUBLE PRECISION,
                    open_interest DOUBLE PRECISION,
                    bid1_price DOUBLE PRECISION,
                    ask1_price DOUBLE PRECISION,
                    bid1_size DOUBLE PRECISION,
                    ask1_size DOUBLE PRECISION,
                    rsi DOUBLE PRECISION
                )
                """
            ).format(sql.Identifier(table_name))
        )
    connection.commit()


def fetch_db_rows(connection: psycopg2.extensions.connection, table_name: str, start_ms: int, end_ms: int) -> dict[int, dict]:
    """Читает строки из таблицы по диапазону времени."""
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    timestamp_utc,
                    symbol,
                    exchange,
                    timeframe,
                    index_price,
                    funding_rate,
                    open_interest,
                    bid1_price,
                    ask1_price,
                    bid1_size,
                    ask1_size,
                    rsi
                FROM {}
                WHERE timestamp_utc BETWEEN %s AND %s
                ORDER BY timestamp_utc
                """
            ).format(sql.Identifier(table_name)),
            (ms_to_datetime(start_ms), ms_to_datetime(end_ms)),
        )
        result = {}
        for row in cursor.fetchall():
            timestamp_ms = int(row[0].timestamp() * 1000)
            result[timestamp_ms] = {
                "timestamp_utc": row[0],
                "symbol": row[1],
                "exchange": row[2],
                "timeframe": row[3],
                "index_price": row[4],
                "funding_rate": row[5],
                "open_interest": row[6],
                "bid1_price": row[7],
                "ask1_price": row[8],
                "bid1_size": row[9],
                "ask1_size": row[10],
                "rsi": row[11],
            }
        return result


def find_missing_timestamps(existing_timestamps: set[int], start_ms: int, end_ms: int, step_ms: int) -> list[int]:
    """Ищет отсутствующие интервалы в таблице."""
    return [ts for ts in range(start_ms, end_ms + step_ms, step_ms) if ts not in existing_timestamps]


def group_missing_ranges(timestamps: list[int], step_ms: int) -> list[tuple[int, int]]:
    """Склеивает соседние пропуски в диапазоны загрузки."""
    if not timestamps:
        return []
    ranges = []
    range_start = timestamps[0]
    previous = timestamps[0]
    for timestamp in timestamps[1:]:
        if timestamp != previous + step_ms:
            ranges.append((range_start, previous))
            range_start = timestamp
        previous = timestamp
    ranges.append((range_start, previous))
    return ranges


def fetch_range_rows(
    category: str,
    symbol: str,
    timeframe: str,
    bybit_interval: str,
    range_start: int,
    range_end: int,
    funding_lookback_ms: int,
    open_interest_interval: tuple[str, int],
    progress_callback=None,
    progress_start_ms: int | None = None,
    progress_end_ms: int | None = None,
) -> dict[int, dict]:
    """Скачивает недостающий диапазон и отдает частичный прогресс загрузки."""
    index_rows = fetch_index_prices(
        category,
        symbol,
        bybit_interval,
        range_start,
        range_end,
        progress_callback=progress_callback,
        progress_start_ms=progress_start_ms,
        progress_end_ms=progress_end_ms,
    )
    timestamps = [timestamp for timestamp, _ in index_rows]
    prices = [price for _, price in index_rows]
    funding_rows = fetch_funding_rates(category, symbol, max(0, range_start - funding_lookback_ms), range_end)
    open_interest_rows = fetch_open_interest(
        category,
        symbol,
        open_interest_interval[0],
        max(0, range_start - open_interest_interval[1]),
        range_end,
    )
    funding_aligned = align_asof(funding_rows, timestamps)
    open_interest_aligned = align_asof(open_interest_rows, timestamps)
    result = {}
    for i, timestamp in enumerate(timestamps):
        result[timestamp] = {
            "timestamp_utc": ms_to_datetime(timestamp),
            "symbol": symbol,
            "exchange": "bybit",
            "timeframe": timeframe,
            "index_price": prices[i],
            "funding_rate": funding_aligned[i],
            "open_interest": open_interest_aligned[i],
            "bid1_price": None,
            "ask1_price": None,
            "bid1_size": None,
            "ask1_size": None,
            "rsi": None,
        }
    return result


def rebuild_rsi(rows: list[dict], period: int) -> None:
    """Пересчитывает RSI для отсортированного ряда."""
    rsi_values = compute_rsi([row["index_price"] for row in rows], period)
    for row, value in zip(rows, rsi_values):
        row["rsi"] = value


def validate_rows(rows: list[dict], period: int) -> dict:
    """Проверяет итоговый набор перед записью."""
    if not rows:
        raise RuntimeError("No rows to write")
    timestamps = [row["timestamp_utc"] for row in rows]
    if timestamps != sorted(timestamps):
        raise RuntimeError("Rows are not sorted by timestamp")
    if len({row["timestamp_utc"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate timestamps detected")
    missing_counts = {
        "index_price": sum(row["index_price"] is None for row in rows),
        "funding_rate": sum(row["funding_rate"] is None for row in rows),
        "open_interest": sum(row["open_interest"] is None for row in rows),
        "bid1_price": sum(row["bid1_price"] is None for row in rows),
        "ask1_price": sum(row["ask1_price"] is None for row in rows),
        "bid1_size": sum(row["bid1_size"] is None for row in rows),
        "ask1_size": sum(row["ask1_size"] is None for row in rows),
        "rsi": sum(row["rsi"] is None for row in rows),
    }
    if missing_counts["index_price"]:
        raise RuntimeError("index_price contains NULL values")
    for row in rows:
        if row["rsi"] is not None and not 0.0 <= row["rsi"] <= 100.0:
            raise RuntimeError("RSI value is out of range")
    if missing_counts["rsi"] < min(period, len(rows)):
        raise RuntimeError("Unexpected RSI warm-up size")
    return {
        "row_count": len(rows),
        "min_timestamp": rows[0]["timestamp_utc"].isoformat(),
        "max_timestamp": rows[-1]["timestamp_utc"].isoformat(),
        "missing_counts": missing_counts,
    }


def upsert_rows(connection: psycopg2.extensions.connection, table_name: str, rows: list[dict]) -> tuple[int, int]:
    """Пишет строки в PostgreSQL через UPSERT."""
    statement = sql.SQL(
        """
        INSERT INTO {} (
            timestamp_utc,
            symbol,
            exchange,
            timeframe,
            index_price,
            funding_rate,
            open_interest,
            bid1_price,
            ask1_price,
            bid1_size,
            ask1_size,
            rsi
        ) VALUES %s
        ON CONFLICT (timestamp_utc)
        DO UPDATE SET
            symbol = EXCLUDED.symbol,
            exchange = EXCLUDED.exchange,
            timeframe = EXCLUDED.timeframe,
            index_price = EXCLUDED.index_price,
            funding_rate = EXCLUDED.funding_rate,
            open_interest = EXCLUDED.open_interest,
            bid1_price = EXCLUDED.bid1_price,
            ask1_price = EXCLUDED.ask1_price,
            bid1_size = EXCLUDED.bid1_size,
            ask1_size = EXCLUDED.ask1_size,
            rsi = EXCLUDED.rsi
        RETURNING (xmax = 0) AS inserted
        """
    ).format(sql.Identifier(table_name))
    statement_sql = statement.as_string(connection)
    inserted = 0
    updated = 0
    for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[offset : offset + UPSERT_BATCH_SIZE]
        values = [
            (
                row["timestamp_utc"],
                row["symbol"],
                row["exchange"],
                row["timeframe"],
                row["index_price"],
                row["funding_rate"],
                row["open_interest"],
                row["bid1_price"],
                row["ask1_price"],
                row["bid1_size"],
                row["ask1_size"],
                row["rsi"],
            )
            for row in batch
        ]
        with connection.cursor() as cursor:
            execute_values(cursor, statement_sql, values, page_size=len(values))
            flags = cursor.fetchall()
        batch_inserted = sum(1 for flag, in flags if flag)
        inserted += batch_inserted
        updated += len(flags) - batch_inserted
    connection.commit()
    return inserted, updated


def print_summary(summary: dict, missing_ranges: list[tuple[int, int]], table_name: str) -> None:
    """Печатает краткую сводку по загрузке."""
    log(f"Table: {table_name}")
    log(f"Rows prepared: {summary['row_count']}")
    log(f"Range: {summary['min_timestamp']} -> {summary['max_timestamp']}")
    log(f"Missing ranges downloaded: {len(missing_ranges)}")
    for start_ms, end_ms in missing_ranges:
        log(f"  {ms_to_datetime(start_ms).isoformat()} -> {ms_to_datetime(end_ms).isoformat()}")
    log(f"Missing values: {summary['missing_counts']}")


def build_argument_parser() -> argparse.ArgumentParser:
    """Создает минимальный CLI для демо-скрипта."""
    parser = argparse.ArgumentParser(description="Build a Bybit demo dataset and store it in PostgreSQL.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="60m")
    parser.add_argument("--category", default="linear", choices=["linear", "inverse"])
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--postgres-user", default=os.getenv("PGUSER"))
    parser.add_argument("--postgres-password", default=os.getenv("PGPASSWORD"))
    parser.add_argument("--rsi-period", type=int, default=14)
    return parser


def main() -> int:
    """Запускает полный demo-поток от Bybit до PostgreSQL."""
    parser = build_argument_parser()
    args = parser.parse_args()
    if not args.postgres_user:
        parser.error("--postgres-user is required unless PGUSER is set")
    if args.postgres_password is None:
        parser.error("--postgres-password is required unless PGPASSWORD is set")

    symbol = args.symbol.upper().strip()
    timeframe, bybit_interval, step_ms = normalize_timeframe(args.timeframe)
    start_ms, end_ms = normalize_window(
        parse_timestamp_to_ms(args.start),
        parse_timestamp_to_ms(args.end),
        step_ms,
    )
    launch_time_ms, funding_lookback_ms = fetch_instrument_details(args.category, symbol)
    if launch_time_ms:
        start_ms = max(start_ms, ceil_to_step(launch_time_ms, step_ms))
        if start_ms > end_ms:
            raise RuntimeError("Requested range is before the instrument launch time")

    table_name = make_table_name(symbol, timeframe)
    open_interest_interval = choose_open_interest_interval(step_ms)

    try:
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=args.postgres_user,
            password=args.postgres_password,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to connect to PostgreSQL database {DB_NAME} on {DB_HOST}:{DB_PORT}: {exc}"
        ) from exc

    try:
        existed = table_exists(connection, table_name)
        ensure_table(connection, table_name)
        if existed:
            log(f"Table {table_name} already exists.")
        else:
            log(f"Table {table_name} created.")

        requested_rows = fetch_db_rows(connection, table_name, start_ms, end_ms)
        missing_requested = find_missing_timestamps(set(requested_rows), start_ms, end_ms, step_ms)
        if not missing_requested:
            log("No missing intervals found. Nothing to download.")
            return 0

        refresh_start = max(start_ms, missing_requested[0] - args.rsi_period * step_ms)
        combined_rows = fetch_db_rows(connection, table_name, refresh_start, end_ms)
        missing_refresh = find_missing_timestamps(set(combined_rows), refresh_start, end_ms, step_ms)
        missing_ranges = group_missing_ranges(missing_refresh, step_ms)

        for range_start, range_end in missing_ranges:
            combined_rows.update(
                fetch_range_rows(
                    args.category,
                    symbol,
                    timeframe,
                    bybit_interval,
                    range_start,
                    range_end,
                    funding_lookback_ms,
                    open_interest_interval,
                )
            )

        ordered_timestamps = list(range(refresh_start, end_ms + step_ms, step_ms))
        still_missing = [timestamp for timestamp in ordered_timestamps if timestamp not in combined_rows]
        if still_missing:
            raise RuntimeError(
                f"Bybit did not return full coverage for {len(still_missing)} timestamps starting at "
                f"{ms_to_datetime(still_missing[0]).isoformat()}"
            )

        rows_to_write = [combined_rows[timestamp] for timestamp in ordered_timestamps]
        rebuild_rsi(rows_to_write, args.rsi_period)
        summary = validate_rows(rows_to_write, args.rsi_period)
        print_summary(summary, missing_ranges, table_name)

        if all(summary["missing_counts"][field] == len(rows_to_write) for field in ORDERBOOK_FIELDS):
            log(
                "Historical bid/ask level-1 fields are not available from Bybit v5 public history, "
                "so bid1_price, ask1_price, bid1_size and ask1_size stay NULL in this demo."
            )

        inserted, updated = upsert_rows(connection, table_name, rows_to_write)
        log(f"Inserted rows: {inserted}")
        log(f"Updated rows: {updated}")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())