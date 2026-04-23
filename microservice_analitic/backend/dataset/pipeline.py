from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import psycopg2

from .api import (
    fetch_funding_rates,
    fetch_index_prices,
    fetch_instrument_details,
    fetch_open_interest,
)
from .timelog import now, tlog
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
from .database import fetch_db_rows_raw, find_missing_timestamps_sql, upsert_dataframe, upsert_rows, validate_database
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
    """Вычисляет RSI по index_price (pandas EWM — Cython, ~50x быстрее pure-Python).

    Использует pandas ewm(alpha=1/period, adjust=False) для Wilder's smoothing,
    что эквивалентно рекурсивной формуле avg = (avg*(period-1) + delta) / period.
    Для 3M строк (btcusdt_1m 2020-2026) выполняется за ~0.1s вместо ~5s.
    """
    if period <= 0:
        raise ValueError("RSI period must be positive")
    n = len(prices)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi

    arr = np.asarray(prices, dtype=np.float64)
    deltas = np.diff(arr)                           # shape (n-1,)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed: SMA of first `period` delta values (standard Wilder initialisation)
    seed_gain = float(gains[:period].mean())
    seed_loss = float(losses[:period].mean())

    alpha = 1.0 / period

    if n > period + 1:
        # Prepend seed so ewm(adjust=False) starts the recursion from it.
        # Result[0] = seed (dropped), result[1..] = Wilder-smoothed values.
        avg_gains = (
            pd.Series(np.concatenate([[seed_gain], gains[period:]]))
            .ewm(alpha=alpha, adjust=False)
            .mean()
            .to_numpy()[1:]
        )
        avg_losses = (
            pd.Series(np.concatenate([[seed_loss], losses[period:]]))
            .ewm(alpha=alpha, adjust=False)
            .mean()
            .to_numpy()[1:]
        )
    else:
        avg_gains  = np.empty(0, dtype=np.float64)
        avg_losses = np.empty(0, dtype=np.float64)

    # rsi[period] from the seed values
    def _scalar(ag: float, al: float) -> float:
        if ag == 0.0 and al == 0.0:
            return 50.0
        return 100.0 if al == 0.0 else 100.0 - 100.0 / (1.0 + ag / al)

    rsi[period] = _scalar(seed_gain, seed_loss)

    # Remaining RSI values — fully vectorized
    if len(avg_gains):
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = np.where(avg_losses == 0.0, np.inf, avg_gains / avg_losses)
        rsi_arr = np.where(
            (avg_gains == 0.0) & (avg_losses == 0.0), 50.0,
            np.where(avg_losses == 0.0, 100.0, 100.0 - 100.0 / (1.0 + rs)),
        )
        for i, v in enumerate(rsi_arr):
            rsi[period + 1 + i] = float(v)

    return rsi


def find_missing_timestamps(existing_timestamps: set[int], start_ms: int, end_ms: int, step_ms: int) -> list[int]:
    """Ищет отсутствующие интервалы в таблице."""
    n_expected = (end_ms - start_ms) // step_ms + 1
    # Быстрый выход: если в сете достаточно элементов, считаем сколько из них
    # попадает в диапазон — это дешевле, чем материализовать весь range().
    if len(existing_timestamps) >= n_expected:
        n_in_range = sum(1 for ts in existing_timestamps if start_ms <= ts <= end_ms)
        if n_in_range >= n_expected:
            return []
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
    """Скачивает недостающий диапазон и отдает частичный прогресс загрузки.

    index_price, funding_rate и open_interest загружаются параллельно,
    что сокращает время ожидания от суммы до максимума трёх веток.
    """
    t0 = now()
    tlog.info(
        "fetch_range_rows | START symbol=%s interval=%s range=[%d,%d]",
        symbol, bybit_interval, range_start, range_end,
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_index = executor.submit(
            fetch_index_prices,
            category,
            symbol,
            bybit_interval,
            range_start,
            range_end,
            progress_callback,
            progress_start_ms,
            progress_end_ms,
        )
        f_funding = executor.submit(
            fetch_funding_rates,
            category,
            symbol,
            max(0, range_start - funding_lookback_ms),
            range_end,
        )
        f_oi = executor.submit(
            fetch_open_interest,
            category,
            symbol,
            open_interest_interval[0],
            max(0, range_start - open_interest_interval[1]),
            range_end,
        )
        try:
            index_rows = f_index.result()
        except Exception:
            tlog.exception(
                "fetch_range_rows | index_prices FAILED symbol=%s interval=%s wall=%.3fs",
                symbol, bybit_interval, now() - t0,
            )
            raise
        tlog.info("fetch_range_rows | index_prices done rows=%d wall=%.3fs", len(index_rows), now() - t0)
        try:
            funding_rows = f_funding.result()
        except Exception:
            tlog.exception(
                "fetch_range_rows | funding_rates FAILED symbol=%s interval=%s wall=%.3fs",
                symbol, bybit_interval, now() - t0,
            )
            raise
        tlog.info("fetch_range_rows | funding_rates done rows=%d wall=%.3fs", len(funding_rows), now() - t0)
        try:
            open_interest_rows = f_oi.result()
        except Exception:
            tlog.exception(
                "fetch_range_rows | open_interest FAILED symbol=%s interval=%s wall=%.3fs",
                symbol, bybit_interval, now() - t0,
            )
            raise
        tlog.info("fetch_range_rows | open_interest done rows=%d wall=%.3fs", len(open_interest_rows), now() - t0)

    timestamps = [timestamp for timestamp, _ in index_rows]
    prices = [price for _, price in index_rows]
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
    tlog.info(
        "fetch_range_rows | DONE symbol=%s candles=%d total_elapsed=%.3fs",
        symbol, len(result), now() - t0,
    )
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
    # Единственный проход по строкам: считаем None и проверяем RSI диапазон.
    missing_counts: dict[str, int] = {"index_price": 0, "funding_rate": 0, "open_interest": 0, "rsi": 0}
    rsi_out_of_range = False
    for row in rows:
        if row["index_price"] is None:
            missing_counts["index_price"] += 1
        if row["funding_rate"] is None:
            missing_counts["funding_rate"] += 1
        if row["open_interest"] is None:
            missing_counts["open_interest"] += 1
        rsi = row["rsi"]
        if rsi is None:
            missing_counts["rsi"] += 1
        elif not 0.0 <= rsi <= 100.0:
            rsi_out_of_range = True
    if missing_counts["index_price"]:
        raise RuntimeError("index_price contains NULL values")
    if rsi_out_of_range:
        raise RuntimeError("RSI value is out of range")
    if missing_counts["rsi"] > min(period, len(rows)):
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
    on_upsert_batch=None,
    write_start_ms: int | None = None,
) -> tuple[dict, int, int]:
    """Пересчитывает RSI и сразу сохраняет строки в PostgreSQL.

    ``write_start_ms`` — если задан, строки с timestamp_utc ДО этого момента
    используются только как warm-up контекст для RSI и в БД не записываются.
    Это предотвращает перезапись валидного RSI у «хвостовых» строк предыдущей
    загрузки, которые попали в окно warm-up текущей загрузки.

    ``on_upsert_batch(written, total)`` вызывается после каждого батча upsert,
    если передан — позволяет UI обновлять прогресс-бар во время записи.
    """
    t0 = now()
    tlog.info("rebuild_rsi_and_upsert | START table=%s rows=%d write_start_ms=%s", table_name, len(rows), write_start_ms)
    rebuild_rsi(rows, period)
    tlog.info("rebuild_rsi_and_upsert | rsi done elapsed=%.3fs", now() - t0)
    summary = validate_rows(rows, period)
    if add_features:
        t_feat = now()
        features_frame = pd.DataFrame(rows)
        features_frame["timestamp_utc"] = pd.to_datetime(features_frame["timestamp_utc"], utc=True)
        features_frame = build_features(features_frame, add_target=True, warmup_candles=0)
        if write_start_ms is not None:
            _write_start_dt = ms_to_datetime(write_start_ms)
            features_frame = features_frame[
                features_frame["timestamp_utc"] >= _write_start_dt
            ].reset_index(drop=True)
            tlog.info("rebuild_rsi_and_upsert | trimmed to write_start rows=%d", len(features_frame))
        tlog.info("rebuild_rsi_and_upsert | features done rows=%d elapsed=%.3fs", len(features_frame), now() - t_feat)
        t_db = now()
        # DataFrame → COPY напрямую: C-level to_csv избегает to_dict + per-row цикла
        inserted, updated = upsert_dataframe(connection, table_name, features_frame, on_batch=on_upsert_batch)
    else:
        if write_start_ms is not None:
            _write_start_dt = ms_to_datetime(write_start_ms)
            rows = [r for r in rows if r["timestamp_utc"] >= _write_start_dt]
        t_db = now()
        inserted, updated = upsert_rows(connection, table_name, rows, on_batch=on_upsert_batch)
    tlog.info(
        "rebuild_rsi_and_upsert | upsert done inserted=%d updated=%d db_elapsed=%.3fs total_elapsed=%.3fs",
        inserted, updated, now() - t_db, now() - t0,
    )
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

        # SQL generate_series находит пропуски целиком на стороне PG —
        # для полностью загруженного датасета никаких строк по сети не передаётся.
        missing_requested = find_missing_timestamps_sql(connection, table_name, start_ms, end_ms, step_ms)
        if not missing_requested:
            log("No missing intervals found. Refreshing RSI seed window.")

        refresh_start = max(ceil_to_step(launch_time_ms, step_ms), start_ms - args.rsi_period * step_ms)
        combined_rows = fetch_db_rows_raw(connection, table_name, refresh_start, end_ms)
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
            write_start_ms=start_ms,
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
