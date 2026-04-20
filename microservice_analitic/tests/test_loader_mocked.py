"""Tests for backend.model.loader — with mocked psycopg2 connection."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from backend.model.loader import (
    _validate_features,
    list_target_candidates,
    load_training_data,
)


def _make_conn(rows, columns):
    """Returns a mock psycopg2 connection that returns given rows/columns."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.description = [(col,) for col in columns]
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


# ---------------------------------------------------------------------------
# list_target_candidates
# ---------------------------------------------------------------------------

def test_list_target_candidates_returns_columns():
    conn, cursor = _make_conn([("target_return_1",), ("target_return_3",)], ["column_name"])
    result = list_target_candidates(conn, "btcusdt_60m")
    assert "target_return_1" in result
    assert "target_return_3" in result


def test_list_target_candidates_empty():
    conn, cursor = _make_conn([], ["column_name"])
    result = list_target_candidates(conn, "no_targets_table")
    assert result == []


# ---------------------------------------------------------------------------
# load_training_data
# ---------------------------------------------------------------------------

def _make_df_rows(n: int = 300) -> tuple[list[tuple], list[str]]:
    """Creates fake rows and column names for a training dataset."""
    columns = [
        "timestamp_utc", "symbol", "exchange", "timeframe",
        "index_price", "feat_a", "feat_b", "target_return_1",
    ]
    ts_base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        ts = datetime(2024, 1, 1, i % 24, tzinfo=timezone.utc)
        row = (ts, "BTCUSDT", "bybit", "60m", 40000.0 + i, float(i), float(i) * 0.5,
               None if i >= n - 1 else float(i) * 0.01)
        rows.append(row)
    return rows, columns


def test_load_training_data_success():
    rows, columns = _make_df_rows(300)
    conn, _ = _make_conn(rows, columns)
    X, y, feature_cols, timestamps = load_training_data(conn, "btcusdt_60m")
    assert isinstance(X, pd.DataFrame)
    assert isinstance(y, pd.Series)
    assert len(X) == len(y)
    assert len(X) >= 200  # min_rows default


def test_load_training_data_empty_table_raises():
    conn, _ = _make_conn([], ["timestamp_utc", "symbol", "target_return_1"])
    with pytest.raises(ValueError, match="пуста"):
        load_training_data(conn, "empty_table")


def test_load_training_data_missing_target_raises():
    rows, columns = _make_df_rows(300)
    conn, _ = _make_conn(rows, columns)
    with pytest.raises(ValueError, match="отсутствует"):
        load_training_data(conn, "btcusdt_60m", target_col="target_missing")


def test_load_training_data_not_enough_rows_raises():
    rows, columns = _make_df_rows(50)  # fewer than min_rows=200
    conn, _ = _make_conn(rows, columns)
    with pytest.raises(ValueError, match="Недостаточно данных"):
        load_training_data(conn, "btcusdt_60m")


def test_load_training_data_with_date_filter():
    rows, columns = _make_df_rows(300)
    conn, _ = _make_conn(rows, columns)
    X, y, _, _ = load_training_data(
        conn, "btcusdt_60m",
        date_from="2024-01-01", date_to="2024-12-31",
    )
    assert len(X) > 0


def test_load_training_data_custom_target():
    rows, columns = _make_df_rows(300)
    conn, _ = _make_conn(rows, columns)
    X, y, _, _ = load_training_data(conn, "btcusdt_60m", target_col="target_return_1")
    assert len(y) > 0


# ---------------------------------------------------------------------------
# _validate_features
# ---------------------------------------------------------------------------

def test_validate_features_removes_all_nan():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [float("nan")] * 3})
    result = _validate_features(df, ["a", "b"])
    assert "b" not in result
    assert "a" in result


def test_validate_features_removes_constant():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [5.0, 5.0, 5.0]})
    result = _validate_features(df, ["a", "b"])
    assert "b" not in result


def test_validate_features_warns_high_nan(capsys):
    n = 100
    # 40% NaN but non-constant non-NaN values → kept with warning
    vals = [float("nan")] * 40 + list(np.arange(1.0, 61.0))
    df = pd.DataFrame({"a": np.arange(n, dtype=float), "b": vals})
    result = _validate_features(df, ["a", "b"])
    assert "b" in result  # kept despite high NaN fraction


def test_validate_features_empty_df():
    result = _validate_features(pd.DataFrame(), [])
    assert result == []
