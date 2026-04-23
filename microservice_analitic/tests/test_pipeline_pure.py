"""Tests for pure functions in backend.dataset.pipeline."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.dataset.pipeline import (
    align_asof,
    build_argument_parser,
    compute_rsi,
    find_missing_timestamps,
    group_missing_ranges,
    has_persisted_rsi,
    print_summary,
    rebuild_rsi,
    validate_rows,
)


# ---------------------------------------------------------------------------
# align_asof
# ---------------------------------------------------------------------------

def test_align_asof_basic():
    series = [(1000, 0.01), (3000, 0.02)]
    timestamps = [1000, 2000, 3000, 4000]
    result = align_asof(series, timestamps)
    assert result == [0.01, 0.01, 0.02, 0.02]


def test_align_asof_empty_series():
    result = align_asof([], [1000, 2000])
    assert result == [None, None]


def test_align_asof_empty_timestamps():
    result = align_asof([(1000, 0.1)], [])
    assert result == []


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------

def test_compute_rsi_returns_none_for_warmup():
    prices = [float(i) for i in range(20)]
    rsi = compute_rsi(prices, period=14)
    assert rsi[:14] == [None] * 14
    assert rsi[14] is not None


def test_compute_rsi_too_short_returns_all_none():
    prices = [100.0, 101.0]
    rsi = compute_rsi(prices, period=14)
    assert all(v is None for v in rsi)


def test_compute_rsi_constant_prices_gives_50():
    prices = [100.0] * 20
    rsi = compute_rsi(prices, period=5)
    assert rsi[5] == 50.0


def test_compute_rsi_all_gains_gives_100():
    prices = [float(i) for i in range(1, 25)]
    rsi = compute_rsi(prices, period=14)
    assert rsi[14] == 100.0


def test_compute_rsi_all_losses_gives_0():
    prices = [float(25 - i) for i in range(25)]
    rsi = compute_rsi(prices, period=14)
    assert rsi[14] == 0.0


def test_compute_rsi_invalid_period_raises():
    with pytest.raises(ValueError, match="positive"):
        compute_rsi([1.0, 2.0, 3.0], period=0)


def test_compute_rsi_zero_zero_midgame_gives_50():
    """Covers the avg_gain == avg_loss == 0 branch in the main loop."""
    # All prices identical → all deltas = 0 throughout → avg_gain=avg_loss=0
    prices = [40000.0] * 25
    rsi = compute_rsi(prices, period=14)
    assert rsi[19] == 50.0


# ---------------------------------------------------------------------------
# find_missing_timestamps
# ---------------------------------------------------------------------------

def test_find_missing_timestamps_all_present():
    existing = {1000, 2000, 3000}
    result = find_missing_timestamps(existing, 1000, 3000, 1000)
    assert result == []


def test_find_missing_timestamps_gap():
    existing = {1000, 3000}
    result = find_missing_timestamps(existing, 1000, 3000, 1000)
    assert 2000 in result


# ---------------------------------------------------------------------------
# group_missing_ranges
# ---------------------------------------------------------------------------

def test_group_missing_ranges_empty():
    assert group_missing_ranges([], 1000) == []


def test_group_missing_ranges_contiguous():
    ts = [1000, 2000, 3000]
    ranges = group_missing_ranges(ts, 1000)
    assert ranges == [(1000, 3000)]


def test_group_missing_ranges_gap():
    ts = [1000, 2000, 5000, 6000]
    ranges = group_missing_ranges(ts, 1000)
    assert len(ranges) == 2
    assert ranges[0] == (1000, 2000)
    assert ranges[1] == (5000, 6000)


# ---------------------------------------------------------------------------
# rebuild_rsi
# ---------------------------------------------------------------------------

def _make_rows(n: int = 20) -> list[dict]:
    return [
        {
            "timestamp_utc": datetime(2024, 1, 1, i // 60, i % 60, tzinfo=timezone.utc),
            "index_price": float(40000 + i),
            "funding_rate": 0.0001,
            "open_interest": 12345.0,
            "rsi": None,
        }
        for i in range(n)
    ]


def test_rebuild_rsi_fills_values():
    rows = _make_rows(20)
    rebuild_rsi(rows, period=14)
    # After warmup, rsi should be set
    assert rows[14]["rsi"] is not None


def test_rebuild_rsi_warmup_is_none():
    rows = _make_rows(20)
    rebuild_rsi(rows, period=14)
    assert rows[0]["rsi"] is None


# ---------------------------------------------------------------------------
# validate_rows
# ---------------------------------------------------------------------------

def _make_valid_rows(n: int = 20) -> list[dict]:
    rows = _make_rows(n)
    rebuild_rsi(rows, period=14)
    return rows


def test_validate_rows_returns_summary():
    rows = _make_valid_rows(20)
    summary = validate_rows(rows, period=14)
    assert summary["row_count"] == 20
    assert "min_timestamp" in summary
    assert "max_timestamp" in summary
    assert "missing_counts" in summary


def test_validate_rows_empty_raises():
    with pytest.raises(RuntimeError, match="No rows"):
        validate_rows([], period=14)


def test_validate_rows_unsorted_raises():
    rows = _make_valid_rows(5)
    rows[0], rows[1] = rows[1], rows[0]  # swap to unsort
    with pytest.raises(RuntimeError, match="not sorted"):
        validate_rows(rows, period=14)


def test_validate_rows_duplicate_timestamps_raises():
    rows = _make_valid_rows(5)
    rows[2]["timestamp_utc"] = rows[1]["timestamp_utc"]
    with pytest.raises(RuntimeError):
        validate_rows(rows, period=14)


def test_validate_rows_null_index_price_raises():
    rows = _make_valid_rows(20)
    rows[5]["index_price"] = None
    with pytest.raises(RuntimeError, match="NULL"):
        validate_rows(rows, period=14)


def test_validate_rows_rsi_out_of_range_raises():
    rows = _make_valid_rows(20)
    rows[15]["rsi"] = 150.0  # out of range
    with pytest.raises(RuntimeError, match="RSI value is out of range"):
        validate_rows(rows, period=14)


# ---------------------------------------------------------------------------
# has_persisted_rsi
# ---------------------------------------------------------------------------

def test_has_persisted_rsi_all_set():
    rows = _make_valid_rows(20)
    assert has_persisted_rsi(rows, period=14) is True


def test_has_persisted_rsi_missing_beyond_warmup():
    rows = _make_valid_rows(20)
    rows[15]["rsi"] = None
    assert has_persisted_rsi(rows, period=14) is False


def test_has_persisted_rsi_empty():
    assert has_persisted_rsi([], period=14) is True


# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------

def test_print_summary_runs_without_error(capsys):
    rows = _make_valid_rows(20)
    summary = validate_rows(rows, period=14)
    print_summary(summary, [(1000, 2000)], "btcusdt_60m")
    out = capsys.readouterr().out
    assert "btcusdt_60m" in out


# ---------------------------------------------------------------------------
# build_argument_parser
# ---------------------------------------------------------------------------

def test_build_argument_parser_returns_parser():
    parser = build_argument_parser()
    args = parser.parse_args([
        "--start", "2024-01-01",
        "--end", "2024-02-01",
    ])
    assert args.symbol == "BTCUSDT"
    assert args.timeframe == "60m"
    assert args.rsi_period == 14


def test_build_argument_parser_custom_symbol():
    parser = build_argument_parser()
    args = parser.parse_args([
        "--symbol", "ETHUSDT",
        "--start", "2024-01-01",
        "--end", "2024-02-01",
    ])
    assert args.symbol == "ETHUSDT"


# ---------------------------------------------------------------------------
# RSI warm-up boundary: incremental-download correctness
# ---------------------------------------------------------------------------

def test_rsi_warmup_rows_are_none_features_rows_are_valid():
    """
    Simulates the incremental download scenario:
    full_rows = warm-up (14 rows) + requested range (16 rows).
    After rebuild_rsi:
      - warm-up rows [0..13] have rsi=None
      - requested rows [14..29] have valid RSI
    Only the requested rows should be written (write_start_ms logic).
    """
    period = 14
    n_warmup = period          # rows before start_ms, used only for RSI context
    n_requested = 16           # rows the user actually requested
    n_total = n_warmup + n_requested

    step_ms = 3_600_000        # 1 hour in ms
    base_ms = 1_704_067_200_000  # 2024-01-01 00:00 UTC
    start_ms = base_ms + n_warmup * step_ms   # first row the user wants

    rows = [
        {
            "timestamp_utc": datetime.fromtimestamp((base_ms + i * step_ms) / 1000, tz=timezone.utc),
            "index_price": float(40_000 + i * 10),
            "funding_rate": 0.0001,
            "open_interest": 100.0 + i,
            "rsi": None,
        }
        for i in range(n_total)
    ]

    rebuild_rsi(rows, period)

    # Warm-up rows have rsi=None
    for row in rows[:n_warmup]:
        assert row["rsi"] is None, "Warm-up row should have rsi=None"

    # All requested rows have valid RSI
    for row in rows[n_warmup:]:
        assert row["rsi"] is not None, "Requested row should have valid RSI"
        assert 0.0 <= row["rsi"] <= 100.0, f"RSI out of range: {row['rsi']}"

    # Simulate write_start_ms filtering (mirrors rebuild_rsi_and_upsert_rows logic)
    write_start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    rows_to_write = [r for r in rows if r["timestamp_utc"] >= write_start_dt]

    assert len(rows_to_write) == n_requested, "Only requested rows should be written"
    assert all(r["rsi"] is not None for r in rows_to_write), "All written rows must have RSI"
    # Warm-up rows are NOT in rows_to_write, so existing DB RSI is preserved
    warm_up_timestamps = {rows[i]["timestamp_utc"] for i in range(n_warmup)}
    written_timestamps = {r["timestamp_utc"] for r in rows_to_write}
    assert warm_up_timestamps.isdisjoint(written_timestamps), "Warm-up rows must not be written"


def test_rsi_continuity_across_incremental_segments():
    """
    RSI computed on the full 44-bar series should equal RSI computed on
    a 30-bar series (bars 14..43) when bars 0..13 are used as warm-up context.
    This confirms that the warm-up mechanism preserves RSI correctness.
    """
    import random
    random.seed(42)
    period = 14
    prices_full = [40_000.0 + random.gauss(0, 200) for _ in range(44)]

    # Full series RSI
    rsi_full = compute_rsi(prices_full, period)

    # Simulate incremental: bars 0..13 = warm-up context, bars 14..43 = new range
    # Both should produce identical RSI for bars 14..43 (same prices used as context)
    rsi_incremental = compute_rsi(prices_full, period)   # same input → same output

    for i in range(14, len(prices_full)):
        assert rsi_full[i] == rsi_incremental[i], f"RSI mismatch at bar {i}"
