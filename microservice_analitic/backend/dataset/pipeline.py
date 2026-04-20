from __future__ import annotations

import argparse
import os

import pandas as pd
import psycopg2

from .api import (
    fetch_funding_rates,
    fetch_index_prices,
    fetch_instrument_details,
    fetch_open_interest,
)
from .constants import DB_HOST, DB_NAME, DB_PORT
from .core import (
    ceil_to_step,
    choose_open_interest_interval,
    log,
    make_table_name,
    ms_to_datetime,
    normalize_timeframe,
    normalize_window,
    parse_timestamp_to_ms,
)
from .database import fetch_db_rows, upsert_rows, validate_database
from .features import build_features


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
    for index in range(1, period + 1):
        delta = prices[index] - prices[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rsi[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    if avg_gain == 0 and avg_loss == 0:
        rsi[period] = 50.0
    for index in range(period + 1, len(prices)):
        delta = prices[index] - prices[index - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_gain == 0 and avg_loss == 0:
            rsi[index] = 50.0
        elif avg_loss == 0:
            rsi[index] = 100.0
        else:
            rsi[index] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return rsi


def find_missing_timestamps(existing_timestamps: set[int], start_ms: int, end_ms: int, step_ms: int) -> list[int]:
    """Ищет отсутствующие интервалы в таблице."""
    return [timestamp for timestamp in range(start_ms, end_ms + step_ms, step_ms) if timestamp not in existing_timestamps]


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
    for index, timestamp in enumerate(timestamps):
        result[timestamp] = {
            "timestamp_utc": ms_to_datetime(timestamp),
            "symbol": symbol,
            "exchange": "bybit",
            "timeframe": timeframe,
            "index_price": prices[index],
            "funding_rate": funding_aligned[index],
            "open_interest": open_interest_aligned[index],
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


def has_persisted_rsi(rows: list[dict], period: int) -> bool:
    """Проверяет, что RSI уже сохранен, кроме допустимого warm-up."""
    if not rows:
        return True
    warmup_size = min(period, len(rows))
    return all(row["rsi"] is not None for row in rows[warmup_size:])


def rebuild_rsi_and_upsert_rows(
    connection: psycopg2.extensions.connection,
    table_name: str,
    rows: list[dict],
    period: int,
    add_features: bool = True,
) -> tuple[dict, int, int]:
    """Пересчитывает RSI и сразу сохраняет строки в PostgreSQL."""
    rebuild_rsi(rows, period)
    summary = validate_rows(rows, period)
    if add_features:
        features_frame = pd.DataFrame(rows)
        features_frame["timestamp_utc"] = pd.to_datetime(features_frame["timestamp_utc"], utc=True)
        features_frame = build_features(features_frame, add_target=True, warmup_candles=0)
        features_frame = features_frame.where(pd.notna(features_frame), None)
        rows = []
        for record in features_frame.to_dict("records"):
            timestamp_value = record.get("timestamp_utc")
            if hasattr(timestamp_value, "to_pydatetime"):
                record["timestamp_utc"] = timestamp_value.to_pydatetime()
            rows.append(record)
    inserted, updated = upsert_rows(connection, table_name, rows)
    return summary, inserted, updated


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
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Не вычислять признаки после загрузки данных.",
    )
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
        validate_database(connection, table_name)

        requested_rows = fetch_db_rows(connection, table_name, start_ms, end_ms)
        missing_requested = find_missing_timestamps(set(requested_rows), start_ms, end_ms, step_ms)
        if not missing_requested:
            log("No missing intervals found. Refreshing RSI seed window.")

        refresh_start = max(ceil_to_step(launch_time_ms, step_ms), start_ms - args.rsi_period * step_ms)
        combined_rows = fetch_db_rows(connection, table_name, refresh_start, end_ms)
        if not missing_requested:
            persisted_rows = [combined_rows[timestamp] for timestamp in sorted(combined_rows)]
            if has_persisted_rsi(persisted_rows, args.rsi_period):
                log("No missing intervals found. RSI loaded from PostgreSQL.")
                return 0
            log("No missing intervals found. Computing and saving missing RSI.")

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
        summary, inserted, updated = rebuild_rsi_and_upsert_rows(
            connection,
            table_name,
            rows_to_write,
            args.rsi_period,
            add_features=not args.skip_features,
        )
        print_summary(summary, missing_ranges, table_name)
        log(f"Inserted rows: {inserted}")
        log(f"Updated rows: {updated}")
        if not args.skip_features:
            log("Признаки сохранены в основные столбцы таблицы датасета.")
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
