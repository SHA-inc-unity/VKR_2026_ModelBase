"""Dataset database access layer — Kafka edition.

All database operations are delegated to microservice_data via Kafka
(cmd.data.dataset.*).  This module keeps the same public API (function
names and return types) as the former psycopg2 implementation so that
existing callers require minimal changes.

Write operations (upsert_rows / upsert_dataframe) are NOT supported in
this layer — data ingestion is the responsibility of microservice_data,
which can be triggered via ``data_client.ingest()``.  Callers that still
require write-back should either:
  1. Call ``backend.data_client.ingest(symbol, timeframe, start_ms, end_ms)``
     to ask microservice_data to fetch and store data from the exchange.
  2. Wait for a future ``cmd.data.dataset.upsert`` topic to be added to
     microservice_data (see TODO comments below).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .core import log
from .timelog import now, tlog
from .. import data_client

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_to_ms(value: str | int | datetime) -> int:
    """Convert a timestamp_utc value returned by the Kafka layer to Unix ms."""
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    # ISO-8601 string from .NET JSON serialization ("Z" or "+00:00" suffix)
    s = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# 8 raw columns used for RSI warm-up (subset fetch)
_RAW_COL_SET: frozenset[str] = frozenset((
    "timestamp_utc",
    "symbol",
    "exchange",
    "timeframe",
    "index_price",
    "funding_rate",
    "open_interest",
    "rsi",
))


# ---------------------------------------------------------------------------
# Schema / existence checks
# ---------------------------------------------------------------------------

def table_exists(table_name: str) -> bool:
    """Return True if the table exists in the remote database.

    Uses cmd.data.dataset.coverage — an empty/missing table returns None.
    """
    try:
        coverage = data_client.get_coverage(table_name)
        return coverage is not None
    except Exception:
        return False


def read_table_schema(table_name: str) -> list[tuple[str, str]]:
    """Return (column_name, data_type) pairs for the given table.

    Uses cmd.data.dataset.table_schema.
    """
    schema = data_client.get_schema(table_name)
    return [(col.get("name", ""), col.get("type", "")) for col in schema]


# ---------------------------------------------------------------------------
# DDL helpers — no-op (schema owned by microservice_data)
# ---------------------------------------------------------------------------

def create_market_table(
    table_name: str,
    if_not_exists: bool = False,
) -> None:
    """No-op: table schema is managed by microservice_data.

    In the former psycopg2 implementation this issued a CREATE TABLE.
    Now microservice_data is the sole owner of the PostgreSQL schema.
    """
    log(f"[database] create_market_table: no-op for {table_name!r} (schema owned by microservice_data)")


def ensure_dataset_schema(table_name: str) -> tuple[list[str], list[str]]:
    """No-op: schema migrations are performed by microservice_data.

    Returns (added_columns=[], dropped_columns=[]).
    """
    log(f"[database] ensure_dataset_schema: no-op for {table_name!r}")
    return [], []


def ensure_table(table_name: str) -> None:
    """No-op: table creation is handled by microservice_data."""
    log(f"[database] ensure_table: no-op for {table_name!r}")


def validate_database(table_name: str = "market_data") -> dict:
    """Return a minimal validation report based on coverage metadata.

    In the former psycopg2 implementation this ran DDL + DELETE queries.
    Now it delegates to the coverage Kafka endpoint for read-only health info.
    Data cleanup (NULL rows, duplicates) is handled by microservice_data.
    """
    coverage = data_client.get_coverage(table_name)
    schema = data_client.get_schema(table_name) if coverage else []
    report = {
        "table_name": table_name,
        "table_dropped": False,
        "table_recreated": False,
        "added_columns": [],
        "dropped_columns": [],
        "deleted_null_rows": 0,
        "deleted_duplicate_rows": 0,
        "schema": [(col.get("name", ""), col.get("type", "")) for col in schema],
        "coverage": coverage,
    }
    log(f"[database] validate_database: {table_name!r} coverage={coverage}")
    return report


# ---------------------------------------------------------------------------
# Read operations (delegated to data_client / microservice_data)
# ---------------------------------------------------------------------------

def fetch_db_rows(
    table_name: str,
    start_ms: int,
    end_ms: int,
) -> dict[int, dict]:
    """Return all columns for rows in [start_ms, end_ms] as {ts_ms: row_dict}.

    Uses cmd.data.dataset.rows.
    """
    t0 = now()
    rows = data_client.get_rows(table_name, start_ms, end_ms)
    result: dict[int, dict] = {}
    for row in rows:
        ts_ms = _iso_to_ms(row["timestamp_utc"])
        result[ts_ms] = row
    tlog.info(
        "fetch_db_rows | table=%s rows=%d elapsed=%.3fs",
        table_name, len(result), now() - t0,
    )
    return result


def fetch_db_rows_raw(
    table_name: str,
    start_ms: int,
    end_ms: int,
) -> dict[int, dict]:
    """Return only the 8 raw columns for rows in [start_ms, end_ms].

    Used for RSI warm-up context where feature columns are not needed.
    Fetches all columns from microservice_data (cmd.data.dataset.rows) and
    filters client-side to the 8 raw columns.

    NOTE: If microservice_data adds a ``raw_only`` flag to the rows topic,
    use it to reduce network traffic for large datasets.
    """
    t0 = now()
    rows = data_client.get_rows(table_name, start_ms, end_ms)
    result: dict[int, dict] = {}
    raw_col_set = _RAW_COL_SET
    for row in rows:
        ts_ms = _iso_to_ms(row["timestamp_utc"])
        result[ts_ms] = {k: v for k, v in row.items() if k in raw_col_set}
    tlog.info(
        "fetch_db_rows_raw | table=%s rows=%d elapsed=%.3fs",
        table_name, len(result), now() - t0,
    )
    return result


def fetch_db_timestamps(
    table_name: str,
    start_ms: int,
    end_ms: int,
) -> set[int]:
    """Return the set of timestamps present in the table for [start_ms, end_ms].

    Uses cmd.data.dataset.timestamps.
    """
    timestamps = data_client.get_timestamps(table_name, start_ms, end_ms)
    return set(timestamps)


def find_missing_timestamps_sql(
    table_name: str,
    start_ms: int,
    end_ms: int,
    step_ms: int,
) -> list[int]:
    """Return list of missing timestamps in [start_ms, end_ms] given step_ms.

    Uses cmd.data.dataset.find_missing (delegated to microservice_data which
    uses PostgreSQL generate_series for efficient gap detection).
    """
    return data_client.find_missing(table_name, start_ms, end_ms, step_ms)


# ---------------------------------------------------------------------------
# No-op / stub operations
# ---------------------------------------------------------------------------

def prewarm_table(table_name: str) -> int:
    """No-op: pg_prewarm is managed by microservice_data.

    Returns 0 (no pages prewarmed by this service).
    """
    log(f"[database] prewarm_table: no-op for {table_name!r} (handled by microservice_data)")
    return 0


def upsert_rows(
    table_name: str,
    rows: list[dict],
    on_batch=None,
) -> tuple[int, int]:
    """Write rows to the database via microservice_data.

    TODO: requires cmd.data.dataset.upsert topic in microservice_data.
          Until that topic exists, direct row upserts are not supported.
          Use data_client.ingest(symbol, timeframe, start_ms, end_ms) to
          trigger microservice_data to fetch and ingest data from the exchange.
    """
    raise NotImplementedError(
        "upsert_rows is not available in the Kafka-only architecture. "
        "Trigger data ingestion via data_client.ingest(symbol, timeframe, start_ms, end_ms). "
        "TODO: add cmd.data.dataset.upsert topic to microservice_data."
    )


def upsert_dataframe(
    table_name: str,
    df: pd.DataFrame,
    on_batch=None,
) -> tuple[int, int]:
    """Write a DataFrame to the database via microservice_data.

    TODO: requires cmd.data.dataset.upsert topic in microservice_data.
          Until that topic exists, direct DataFrame upserts are not supported.
          Use data_client.ingest(symbol, timeframe, start_ms, end_ms) to
          trigger microservice_data to fetch and ingest data from the exchange.
    """
    raise NotImplementedError(
        "upsert_dataframe is not available in the Kafka-only architecture. "
        "Trigger data ingestion via data_client.ingest(symbol, timeframe, start_ms, end_ms). "
        "TODO: add cmd.data.dataset.upsert topic to microservice_data."
    )
