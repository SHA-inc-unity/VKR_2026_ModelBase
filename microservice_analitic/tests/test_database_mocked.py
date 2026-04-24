"""Tests for backend.dataset.database — all functions with mocked psycopg2."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.dataset.database import (
    create_market_table,
    ensure_dataset_schema,
    ensure_table,
    fetch_db_rows,
    fetch_db_timestamps,
    find_missing_timestamps_sql,
    read_table_schema,
    table_exists,
    upsert_rows,
    validate_database,
)


def _make_conn():
    """Returns a (connection, cursor) pair of MagicMocks."""
    cursor = MagicMock()
    cursor.rowcount = 0
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


# ---------------------------------------------------------------------------
# table_exists
# ---------------------------------------------------------------------------

def test_table_exists_returns_true_when_found():
    conn, cursor = _make_conn()
    cursor.fetchone.return_value = ("public.my_table",)
    assert table_exists(conn, "my_table") is True


def test_table_exists_returns_false_when_not_found():
    conn, cursor = _make_conn()
    cursor.fetchone.return_value = (None,)
    assert table_exists(conn, "missing_table") is False


# ---------------------------------------------------------------------------
# read_table_schema
# ---------------------------------------------------------------------------

def test_read_table_schema_returns_list():
    conn, cursor = _make_conn()
    cursor.fetchall.return_value = [("col1", "numeric"), ("col2", "text")]
    schema = read_table_schema(conn, "my_table")
    assert schema == [("col1", "numeric"), ("col2", "text")]


def test_read_table_schema_empty_table():
    conn, cursor = _make_conn()
    cursor.fetchall.return_value = []
    assert read_table_schema(conn, "missing") == []


# ---------------------------------------------------------------------------
# create_market_table
# ---------------------------------------------------------------------------

def test_create_market_table_calls_execute_and_commit():
    conn, cursor = _make_conn()
    create_market_table(conn, "test_table")
    assert cursor.execute.called
    assert conn.commit.called


def test_create_market_table_if_not_exists():
    conn, cursor = _make_conn()
    create_market_table(conn, "test_table", if_not_exists=True)
    assert conn.commit.called


# ---------------------------------------------------------------------------
# ensure_dataset_schema
# ---------------------------------------------------------------------------

def test_ensure_dataset_schema_no_changes():
    from backend.dataset.constants import EXPECTED_TABLE_SCHEMA
    conn, cursor = _make_conn()
    # Return existing schema with no forbidden columns and all expected columns
    cursor.fetchall.return_value = list(EXPECTED_TABLE_SCHEMA)
    added, dropped = ensure_dataset_schema(conn, "test_table")
    assert added == []
    assert dropped == []


def test_ensure_dataset_schema_drops_forbidden_cols():
    from backend.dataset.constants import EXPECTED_TABLE_SCHEMA
    conn, cursor = _make_conn()
    existing = list(EXPECTED_TABLE_SCHEMA) + [("bid1_price", "numeric")]
    cursor.fetchall.return_value = existing
    added, dropped = ensure_dataset_schema(conn, "test_table")
    assert "bid1_price" in dropped


def test_ensure_dataset_schema_adds_missing_cols():
    conn, cursor = _make_conn()
    # Only timestamp_utc in schema — everything else is missing
    cursor.fetchall.return_value = [("timestamp_utc", "timestamp with time zone")]
    added, dropped = ensure_dataset_schema(conn, "test_table")
    assert len(added) > 0


# ---------------------------------------------------------------------------
# ensure_table
# ---------------------------------------------------------------------------

def test_ensure_table_calls_create_and_schema():
    from backend.dataset.constants import EXPECTED_TABLE_SCHEMA
    conn, cursor = _make_conn()
    cursor.fetchall.return_value = list(EXPECTED_TABLE_SCHEMA)
    # Should not raise
    ensure_table(conn, "my_table")
    assert conn.commit.called


# ---------------------------------------------------------------------------
# validate_database
# ---------------------------------------------------------------------------

def test_validate_database_existing_table():
    from backend.dataset.constants import EXPECTED_TABLE_SCHEMA
    conn, cursor = _make_conn()
    cursor.fetchall.return_value = list(EXPECTED_TABLE_SCHEMA)
    cursor.rowcount = 0
    report = validate_database(conn, "market_data")
    assert report["table_name"] == "market_data"
    assert report["deleted_null_rows"] == 0


def test_validate_database_creates_table_when_missing():
    conn, cursor = _make_conn()
    # First call (read_table_schema) returns empty → triggers create_market_table
    # Subsequent calls return proper schema
    from backend.dataset.constants import EXPECTED_TABLE_SCHEMA
    cursor.fetchall.side_effect = [
        [],  # first schema read → table not exist
        list(EXPECTED_TABLE_SCHEMA),  # after create
    ]
    cursor.rowcount = 0
    report = validate_database(conn, "market_data")
    assert report["table_recreated"] is True


# ---------------------------------------------------------------------------
# fetch_db_rows
# ---------------------------------------------------------------------------

def test_fetch_db_rows_returns_dict():
    from backend.dataset.constants import DATASET_COLUMN_NAMES
    conn, cursor = _make_conn()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    row = [ts] + ["BTCUSDT", "bybit", "60m"] + [40000.0] + [None] * (len(DATASET_COLUMN_NAMES) - 5)
    # fetchmany: first call returns one batch, second returns empty (signals end)
    cursor.fetchmany.side_effect = [[row], []]
    cursor.description = [(col,) for col in DATASET_COLUMN_NAMES]
    result = fetch_db_rows(conn, "btcusdt_60m", 0, 2_000_000_000_000)
    assert isinstance(result, dict)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# fetch_db_timestamps
# ---------------------------------------------------------------------------

def test_fetch_db_timestamps_returns_set():
    conn, cursor = _make_conn()
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    cursor.fetchall.return_value = [(ts,)]
    result = fetch_db_timestamps(conn, "btcusdt_60m", 0, 2_000_000_000_000)
    assert isinstance(result, set)
    assert len(result) == 1
    assert int(ts.timestamp() * 1000) in result


def test_fetch_db_timestamps_empty_table():
    conn, cursor = _make_conn()
    cursor.fetchall.return_value = []
    result = fetch_db_timestamps(conn, "btcusdt_60m", 0, 2_000_000_000_000)
    assert result == set()


# ---------------------------------------------------------------------------
# find_missing_timestamps_sql
# ---------------------------------------------------------------------------

def test_find_missing_timestamps_sql_returns_missing():
    conn, cursor = _make_conn()
    step_ms = 3_600_000
    start_ms = 1_704_067_200_000
    missing_ms = start_ms + step_ms
    # Fast-path COUNT(*) returns less than expected_count (3) → fallback to generate_series.
    cursor.fetchone.return_value = (2,)
    cursor.fetchall.return_value = [(missing_ms,)]
    result = find_missing_timestamps_sql(conn, "btcusdt_60m", start_ms, start_ms + 2 * step_ms, step_ms)
    assert missing_ms in result
    assert isinstance(result, list)


def test_find_missing_timestamps_sql_returns_empty_when_full():
    conn, cursor = _make_conn()
    # expected_count = (3_600_000 - 0) // 3_600_000 + 1 = 2; COUNT==2 ⇒ fast-path returns [].
    cursor.fetchone.return_value = (2,)
    cursor.fetchall.return_value = []
    result = find_missing_timestamps_sql(conn, "btcusdt_60m", 0, 3_600_000, 3_600_000)
    assert result == []


def test_find_missing_timestamps_sql_fastpath_skips_generate_series():
    """COUNT(*) == expected_count must short-circuit (no fetchall call)."""
    conn, cursor = _make_conn()
    cursor.fetchone.return_value = (2,)
    cursor.fetchall.return_value = [(12345,)]  # if called, would pollute result
    result = find_missing_timestamps_sql(conn, "btcusdt_60m", 0, 3_600_000, 3_600_000)
    assert result == []
    cursor.fetchall.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_rows
# ---------------------------------------------------------------------------

@patch("backend.dataset.database.sql")
def test_upsert_rows_inserts_and_updates(mock_sql):
    from backend.dataset.constants import DATASET_COLUMN_NAMES
    conn, cursor = _make_conn()
    # Make sql.SQL(...).format(...).as_string(conn) return a plain string
    mock_sql.SQL.return_value.format.return_value.as_string.return_value = "INSERT INTO test ..."
    mock_sql.SQL.return_value.join.return_value = MagicMock()
    mock_sql.Identifier.return_value = MagicMock()
    # 2 rows: 1 inserted (True) + 1 updated (False)
    cursor.fetchall.return_value = [(True,), (False,)]

    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    row = {col: None for col in DATASET_COLUMN_NAMES}
    row["timestamp_utc"] = ts
    row["index_price"] = 40000.0
    row["symbol"] = "BTCUSDT"
    row["exchange"] = "bybit"
    row["timeframe"] = "60m"

    inserted, updated = upsert_rows(conn, "btcusdt_60m", [row, row])
    assert isinstance(inserted, int)
    assert isinstance(updated, int)
    assert conn.commit.called


# ---------------------------------------------------------------------------
# Round 2 Fix A: no-op UPDATE skipping via IS DISTINCT FROM
# ---------------------------------------------------------------------------

def test_upsert_rows_skips_noop_updates_when_fetchall_is_short():
    """Когда merge возвращает меньше строк чем прислано — это skipped no-op UPDATEs.

    WHERE (m.*) IS DISTINCT FROM (EXCLUDED.*) в merge_stmt заставляет PostgreSQL
    пропустить запись строк с идентичными значениями. RETURNING возвращает
    только реально записанные строки, поэтому `skipped = total - len(flags)`.
    Функция должна корректно трактовать такой результат: inserted+updated < total.
    """
    from backend.dataset.constants import DATASET_COLUMN_NAMES
    with patch("backend.dataset.database.sql"):
        conn, cursor = _make_conn()
        # 3 rows sent, но merge вернул только 1 (остальные skipped как no-op)
        cursor.fetchall.return_value = [(False,)]  # единственная реально обновлённая

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        row = {col: None for col in DATASET_COLUMN_NAMES}
        row["timestamp_utc"] = ts
        row["index_price"] = 40000.0
        row["symbol"] = "BTCUSDT"
        row["exchange"] = "bybit"
        row["timeframe"] = "60m"

        inserted, updated = upsert_rows(conn, "btcusdt_60m", [row, row, row])
        # 3 sent → 1 returned → 2 skipped (no-op, values identical)
        assert inserted == 0
        assert updated == 1
        assert conn.commit.called


def test_upsert_rows_merge_stmt_contains_is_distinct_from():
    """SQL merge_stmt должен содержать WHERE ... IS DISTINCT FROM ... — Fix A.

    Иначе no-op UPDATEs не будут пропускаться, и при загрузке на уже
    заполненной БД PostgreSQL будет переписывать миллионы строк с идентичными
    значениями (что мы и наблюдали: updated=3.86M при inserted=595k).
    """
    import inspect
    from backend.dataset import database
    src_rows = inspect.getsource(database.upsert_rows)
    src_df = inspect.getsource(database.upsert_dataframe)
    for src, name in ((src_rows, "upsert_rows"), (src_df, "upsert_dataframe")):
        assert "IS DISTINCT FROM" in src, (
            f"{name}: merge_stmt must include 'IS DISTINCT FROM' to skip no-op UPDATEs"
        )
        assert "AS m" in src, (
            f"{name}: merge_stmt must alias target table for row-comparison"
        )
