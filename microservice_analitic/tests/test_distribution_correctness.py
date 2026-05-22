"""Regression tests for the distribution / contiguous-tail read pipeline.

These cover the Phase-3 fix where ``np.diff(np.log(prices))`` was producing
garbage because the underlying parquet read was striding across row groups
non-contiguously. The contract tested here is:

  * ``read_parquet_contiguous`` returns rows whose adjacency in the input
    timeline is preserved (contiguous tail-slice).
  * Therefore ``np.diff(np.log(prices))`` on the result matches what a
    naive single-process computation on the same tail-slice would yield.
  * When the file fits in the budget the whole table is returned.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.anomaly.session import read_parquet_contiguous


def _write_monotone_parquet(path: Path, n_rows: int, n_row_groups: int) -> None:
    """Write a parquet file with a monotone ``close_price`` series split across
    several row groups. Using a stable arithmetic progression makes any
    "stride across row groups" bug observable: the diff of a true contiguous
    tail-slice is constant, while a strided slice produces 0 for in-group
    diffs and large jumps at the seams.
    """
    df = pd.DataFrame(
        {
            "timestamp_utc": np.arange(n_rows, dtype="int64") * 60_000,
            "close_price":   np.linspace(100.0, 200.0, n_rows, dtype="float64"),
        }
    )
    table = pa.Table.from_pandas(df, preserve_index=False)
    rows_per_rg = max(1, n_rows // n_row_groups)
    pq.write_table(table, path, row_group_size=rows_per_rg, compression="snappy")


def test_contiguous_tail_preserves_adjacency(tmp_path: Path) -> None:
    """The returned slice must be a real tail of the input series."""
    parquet = tmp_path / "monotone.parquet"
    n_rows = 10_000
    _write_monotone_parquet(parquet, n_rows=n_rows, n_row_groups=10)

    budget = 3_000
    df = read_parquet_contiguous(parquet, ["close_price"], budget, n_rows)

    assert len(df) >= budget, "should accumulate at least max_rows rows"
    # Reload the original to find the matching tail slice and confirm
    # equality element-wise.
    full = pd.read_parquet(parquet, columns=["close_price"])
    expected_tail = full["close_price"].to_numpy()[-len(df):]
    np.testing.assert_array_equal(df["close_price"].to_numpy(), expected_tail)


def test_diff_log_returns_match_dense_computation(tmp_path: Path) -> None:
    """Numerical regression test for the original distribution bug."""
    parquet = tmp_path / "monotone.parquet"
    n_rows = 8_000
    _write_monotone_parquet(parquet, n_rows=n_rows, n_row_groups=8)

    budget = 2_500
    df = read_parquet_contiguous(parquet, ["close_price"], budget, n_rows)
    log_returns = np.diff(np.log(df["close_price"].to_numpy()))

    # On a strictly monotone arithmetic progression all log-diffs are
    # finite and strictly positive. A non-contiguous slice would produce
    # zeros (within-group) or large outliers (at row-group seams),
    # neither of which can happen here.
    assert np.all(np.isfinite(log_returns))
    assert np.all(log_returns > 0)
    # Values are smooth — adjacent diffs differ by a tiny amount only.
    assert np.max(np.abs(np.diff(log_returns))) < 1e-3


def test_full_read_when_budget_exceeds_size(tmp_path: Path) -> None:
    """When the file already fits the budget, return the whole thing."""
    parquet = tmp_path / "small.parquet"
    n_rows = 500
    _write_monotone_parquet(parquet, n_rows=n_rows, n_row_groups=4)

    df = read_parquet_contiguous(parquet, ["close_price"], 10_000, n_rows)
    assert len(df) == n_rows


def test_handles_unknown_total(tmp_path: Path) -> None:
    """``total_known=None`` should fall back to parquet metadata."""
    parquet = tmp_path / "monotone.parquet"
    _write_monotone_parquet(parquet, n_rows=4_000, n_row_groups=4)

    df = read_parquet_contiguous(parquet, ["close_price"], 1_000, total_known=None)
    assert len(df) >= 1_000
    # Still contiguous — same tail-equality property.
    full = pd.read_parquet(parquet, columns=["close_price"])
    np.testing.assert_array_equal(
        df["close_price"].to_numpy(),
        full["close_price"].to_numpy()[-len(df):],
    )
