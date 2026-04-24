"""Tests for backend.dataset.export.export_dataset_csv — mocked psycopg2."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.dataset.export import (
    _build_copy_statement,
    _parse_ts,
    export_dataset_csv,
)


# ── _parse_ts ───────────────────────────────────────────────────────────────

def test_parse_ts_none_returns_none():
    assert _parse_ts(None) is None


def test_parse_ts_empty_string_returns_none():
    assert _parse_ts("") is None
    assert _parse_ts("   ") is None


def test_parse_ts_date_string_gets_utc():
    v = _parse_ts("2024-01-01")
    assert v == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_parse_ts_iso_with_z_parsed_as_utc():
    v = _parse_ts("2024-01-01T12:00:00Z")
    assert v == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_naive_datetime_made_utc():
    v = _parse_ts(datetime(2024, 1, 1, 0, 0, 0))
    assert v.tzinfo == timezone.utc


def test_parse_ts_aware_datetime_preserved():
    v = _parse_ts(datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc))
    assert v.tzinfo == timezone.utc


def test_parse_ts_invalid_type_raises():
    with pytest.raises(TypeError):
        _parse_ts(12345)  # type: ignore[arg-type]


# ── _build_copy_statement ───────────────────────────────────────────────────

def _render(stmt) -> str:
    """Flatten a psycopg2.sql.Composed tree into a readable string WITHOUT a live
    connection (quote_ident is C-bound and needs a real conn). Identifiers are
    rendered with manual double-quote escaping — good enough for token asserts.
    """
    from psycopg2 import sql as _s

    def _walk(node) -> str:
        if isinstance(node, _s.SQL):
            return node.string
        if isinstance(node, _s.Identifier):
            # Replicate quote_ident: wrap in quotes, double internal quotes.
            return ".".join('"' + p.replace('"', '""') + '"' for p in node.strings)
        if isinstance(node, _s.Placeholder):
            return "%s"
        if isinstance(node, _s.Composed):
            return "".join(_walk(n) for n in node.seq)
        return str(node)

    return _walk(stmt)


def test_build_copy_no_filters_uses_star():
    stmt, params = _build_copy_statement("btcusdt_5m", None, None, None)
    rendered = _render(stmt)
    assert 'SELECT *' in rendered
    assert '"btcusdt_5m"' in rendered
    assert 'ORDER BY timestamp_utc' in rendered
    assert 'COPY' in rendered and 'TO STDOUT' in rendered
    assert 'WHERE' not in rendered
    assert params == ()


def test_build_copy_with_columns_quotes_each():
    cols = ["timestamp_utc", "index_price", "rsi"]
    stmt, params = _build_copy_statement("btcusdt_1m", None, None, cols)
    rendered = _render(stmt)
    for c in cols:
        assert f'"{c}"' in rendered
    assert params == ()


def test_build_copy_with_start_only_emits_single_where():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    stmt, params = _build_copy_statement("btcusdt_5m", start, None, None)
    rendered = _render(stmt)
    assert 'WHERE' in rendered
    assert 'timestamp_utc >= %s' in rendered
    assert 'timestamp_utc <=' not in rendered
    assert params == (start,)


def test_build_copy_with_both_bounds_emits_and():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 2, 1, tzinfo=timezone.utc)
    stmt, params = _build_copy_statement("t", start, end, None)
    rendered = _render(stmt)
    assert 'timestamp_utc >= %s' in rendered
    assert 'timestamp_utc <= %s' in rendered
    assert ' AND ' in rendered
    assert params == (start, end)


def test_build_copy_identifier_quotes_malicious_table_name():
    # Injection-try: should be quoted as identifier, not treated as SQL.
    stmt, _ = _build_copy_statement('evil"; DROP TABLE x; --', None, None, None)
    rendered = _render(stmt)
    assert 'DROP TABLE x' in rendered  # appears literally inside quoted identifier
    # The dangerous quote must be escaped (doubled) inside identifier
    assert '""' in rendered  # psycopg2 doubles embedded quotes


# ── export_dataset_csv: integration with mocked cursor ─────────────────────

def _make_conn_with_copy(output_csv: str):
    """Build a MagicMock connection whose cursor.copy_expert writes output_csv.

    copy_expert writes bytes to BytesIO (now the real path).  The mock therefore
    encodes output_csv → bytes so buf.getvalue() returns bytes, matching the
    updated export_dataset_csv that returns buf.getvalue() directly.
    """
    cur = MagicMock()
    # mogrify returns bytes; echo back a rendered SQL containing stable tokens
    # so tests can assert against cur.copy_expert's first positional arg.
    def _mogrify(stmt, params=()):  # noqa: ARG001
        from psycopg2 import sql as _s

        def _walk(node) -> str:
            if isinstance(node, _s.SQL):
                return node.string
            if isinstance(node, _s.Identifier):
                return ".".join('"' + p.replace('"', '""') + '"' for p in node.strings)
            if isinstance(node, _s.Placeholder):
                return "%s"
            if isinstance(node, _s.Composed):
                return "".join(_walk(n) for n in node.seq)
            return str(node)

        rendered = _walk(stmt) if not isinstance(stmt, str) else stmt
        return rendered.encode("utf-8")

    cur.mogrify = MagicMock(side_effect=_mogrify)

    # copy_expert writes bytes into the BytesIO buffer (matches real psycopg2 behaviour).
    csv_bytes = output_csv.encode("utf-8") if isinstance(output_csv, str) else output_csv

    def _copy_expert(query, buf):  # noqa: ARG001
        buf.write(csv_bytes)

    cur.copy_expert = MagicMock(side_effect=_copy_expert)

    conn = MagicMock()
    conn.encoding = "utf-8"
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


def test_export_dataset_csv_returns_bytes_header_plus_rows():
    csv_text = "timestamp_utc,index_price\n2024-01-01 00:00:00+00,42000.0\n"
    conn, cur = _make_conn_with_copy(csv_text)
    out = export_dataset_csv(conn, "btcusdt_5m")
    assert isinstance(out, bytes)
    assert out.decode("utf-8") == csv_text
    cur.copy_expert.assert_called_once()
    # First positional arg of copy_expert is the SQL string
    sql_str = cur.copy_expert.call_args.args[0]
    assert "COPY" in sql_str and "TO STDOUT" in sql_str
    assert "FORMAT csv" in sql_str
    assert "HEADER" in sql_str


def test_export_dataset_csv_passes_bounds_through_mogrify():
    conn, cur = _make_conn_with_copy("timestamp_utc\n")
    export_dataset_csv(
        conn,
        "btcusdt_5m",
        start_ts_utc="2024-01-01",
        end_ts_utc="2024-02-01",
    )
    # mogrify is always called; with bounds the params tuple has two datetimes.
    assert cur.mogrify.called
    mogrify_params = cur.mogrify.call_args.args[1]
    assert len(mogrify_params) == 2
    assert all(isinstance(p, datetime) for p in mogrify_params)


def test_export_dataset_csv_without_bounds_passes_empty_params():
    conn, cur = _make_conn_with_copy("timestamp_utc\n")
    export_dataset_csv(conn, "t")
    assert cur.mogrify.called
    mogrify_params = cur.mogrify.call_args.args[1]
    assert mogrify_params == ()


def test_export_dataset_csv_with_explicit_columns():
    conn, cur = _make_conn_with_copy("timestamp_utc,rsi\n")
    export_dataset_csv(conn, "t", columns=["timestamp_utc", "rsi"])
    sql_str = cur.copy_expert.call_args.args[0]
    assert '"timestamp_utc"' in sql_str
    assert '"rsi"' in sql_str
    assert "*" not in sql_str.split("FROM")[0]  # no wildcard in SELECT


def test_export_dataset_csv_empty_result_returns_only_header():
    conn, _ = _make_conn_with_copy("timestamp_utc,index_price\n")
    out = export_dataset_csv(conn, "t")
    assert out == b"timestamp_utc,index_price\n"


def test_export_dataset_csv_exported_from_package():
    from backend.dataset import export_dataset_csv as pkg_export
    assert pkg_export is export_dataset_csv
