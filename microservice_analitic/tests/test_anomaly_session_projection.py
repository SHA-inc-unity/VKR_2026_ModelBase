from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.anomaly.session import read_parquet_bounded


def _write_timestamp_ms_parquet(path: Path, n_rows: int) -> np.ndarray:
    timestamp_ms = np.arange(n_rows, dtype="int64") * 60_000
    table = pa.Table.from_pandas(
        pd.DataFrame(
            {
                "timestamp_ms": timestamp_ms,
                "close_price": np.linspace(100.0, 200.0, n_rows, dtype="float64"),
            }
        ),
        preserve_index=False,
    )
    pq.write_table(table, path, compression="snappy")
    return timestamp_ms


def _write_timestamp_utc_parquet(path: Path, n_rows: int) -> np.ndarray:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    timestamps = [base + timedelta(minutes=i) for i in range(n_rows)]
    expected_ms = np.array([int(ts.timestamp() * 1000) for ts in timestamps], dtype="int64")
    table = pa.Table.from_pandas(
        pd.DataFrame(
            {
                "timestamp_utc": timestamps,
                "close_price": np.linspace(100.0, 200.0, n_rows, dtype="float64"),
            }
        ),
        preserve_index=False,
    )
    pq.write_table(table, path, compression="snappy")
    return expected_ms


def test_bounded_aliases_timestamp_utc_request_to_timestamp_ms_column(tmp_path: Path) -> None:
    parquet = tmp_path / "timestamp-ms.parquet"
    expected_ms = _write_timestamp_ms_parquet(parquet, n_rows=256)

    df = read_parquet_bounded(parquet, ["timestamp_utc", "close_price"], 1_000, total_known=256)

    assert "timestamp_ms" in df.columns
    np.testing.assert_array_equal(df["timestamp_ms"].to_numpy(), expected_ms)


def test_bounded_normalizes_datetime_timestamp_utc_to_timestamp_ms(tmp_path: Path) -> None:
    parquet = tmp_path / "timestamp-utc.parquet"
    expected_ms = _write_timestamp_utc_parquet(parquet, n_rows=128)

    df = read_parquet_bounded(parquet, ["timestamp_ms", "close_price"], 1_000, total_known=128)

    assert "timestamp_ms" in df.columns
    np.testing.assert_array_equal(df["timestamp_ms"].to_numpy(), expected_ms)


def test_bounded_reuses_cached_projection(tmp_path: Path, monkeypatch) -> None:
    parquet = tmp_path / "cached.parquet"
    expected_ms = _write_timestamp_ms_parquet(parquet, n_rows=64)

    calls = 0
    real_read_parquet = pd.read_parquet

    def counted_read_parquet(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counted_read_parquet)

    first = read_parquet_bounded(parquet, ["timestamp_ms", "close_price"], 1_000, total_known=64)
    second = read_parquet_bounded(parquet, ["timestamp_ms", "close_price"], 1_000, total_known=64)

    assert calls == 1
    np.testing.assert_array_equal(first["timestamp_ms"].to_numpy(), expected_ms)
    np.testing.assert_array_equal(second["timestamp_ms"].to_numpy(), expected_ms)
