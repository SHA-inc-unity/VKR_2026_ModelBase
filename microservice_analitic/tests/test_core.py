"""Tests for backend.dataset.core — pure utility functions."""
from __future__ import annotations

import pytest

from backend.dataset.core import (
    ceil_to_step,
    choose_open_interest_interval,
    floor_to_step,
    log,
    make_table_name,
    ms_to_datetime,
    normalize_timeframe,
    normalize_window,
    parse_timestamp_to_ms,
)


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def test_log_prints_without_error(capsys):
    log("hello world")
    captured = capsys.readouterr()
    assert "hello world" in captured.out


# ---------------------------------------------------------------------------
# normalize_timeframe
# ---------------------------------------------------------------------------

def test_normalize_timeframe_valid_60m():
    key, interval, step_ms = normalize_timeframe("60m")
    assert key == "60m"
    assert interval == "60"
    assert step_ms == 3_600_000


def test_normalize_timeframe_alias_1h():
    key, interval, step_ms = normalize_timeframe("1h")
    assert key == "60m"


def test_normalize_timeframe_alias_4h():
    key, interval, step_ms = normalize_timeframe("4h")
    assert key == "240m"


def test_normalize_timeframe_1d():
    key, interval, step_ms = normalize_timeframe("1d")
    assert key == "1d"
    assert interval == "D"


def test_normalize_timeframe_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        normalize_timeframe("999m")


# ---------------------------------------------------------------------------
# parse_timestamp_to_ms
# ---------------------------------------------------------------------------

def test_parse_timestamp_already_ms():
    ts_ms = 1_700_000_000_000
    assert parse_timestamp_to_ms(str(ts_ms)) == ts_ms


def test_parse_timestamp_seconds_converted():
    ts_s = 1_700_000_000
    assert parse_timestamp_to_ms(str(ts_s)) == ts_s * 1000


def test_parse_timestamp_iso_string():
    ms = parse_timestamp_to_ms("2024-01-01T00:00:00Z")
    assert ms == 1_704_067_200_000


def test_parse_timestamp_iso_no_tz():
    ms = parse_timestamp_to_ms("2024-01-01T00:00:00")
    assert ms == 1_704_067_200_000


def test_parse_timestamp_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        parse_timestamp_to_ms("   ")


# ---------------------------------------------------------------------------
# ms_to_datetime
# ---------------------------------------------------------------------------

def test_ms_to_datetime_epoch():
    dt = ms_to_datetime(0)
    assert dt.year == 1970
    assert dt.month == 1
    assert dt.day == 1


def test_ms_to_datetime_known_value():
    ms = 1_704_067_200_000  # 2024-01-01T00:00:00Z
    dt = ms_to_datetime(ms)
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 1


# ---------------------------------------------------------------------------
# floor_to_step / ceil_to_step
# ---------------------------------------------------------------------------

def test_floor_to_step_aligned():
    assert floor_to_step(3_600_000, 3_600_000) == 3_600_000


def test_floor_to_step_rounds_down():
    assert floor_to_step(3_700_000, 3_600_000) == 3_600_000


def test_ceil_to_step_aligned():
    assert ceil_to_step(3_600_000, 3_600_000) == 3_600_000


def test_ceil_to_step_rounds_up():
    assert ceil_to_step(3_600_001, 3_600_000) == 7_200_000


# ---------------------------------------------------------------------------
# normalize_window
# ---------------------------------------------------------------------------

def test_normalize_window_start_after_end_raises():
    with pytest.raises(ValueError, match="earlier than end"):
        normalize_window(1_000, 500, 3_600_000)


def test_normalize_window_no_closed_candles_raises():
    import time
    now_ms = int(time.time() * 1000)
    # Start very close to end so no closed candles exist after floor
    with pytest.raises(RuntimeError, match="No closed candles"):
        normalize_window(now_ms + 3_600_000, now_ms + 7_200_000, 3_600_000)


def test_normalize_window_valid():
    start_ms = 1_704_067_200_000  # 2024-01-01 00:00
    end_ms = start_ms + 86_400_000  # +1 day
    step_ms = 3_600_000
    s, e = normalize_window(start_ms, end_ms, step_ms)
    assert s <= e
    assert s % step_ms == 0
    assert e % step_ms == 0


# ---------------------------------------------------------------------------
# make_table_name
# ---------------------------------------------------------------------------

def test_make_table_name():
    assert make_table_name("BTCUSDT", "60m") == "btcusdt_60m"


def test_make_table_name_lowercases():
    assert make_table_name("ETHUSDT", "1D") == "ethusdt_1d"


# ---------------------------------------------------------------------------
# choose_open_interest_interval
# ---------------------------------------------------------------------------

def test_choose_open_interest_interval_1h():
    label, ms = choose_open_interest_interval(3_600_000)
    assert label == "1h"
    assert ms == 3_600_000


def test_choose_open_interest_interval_5m():
    label, ms = choose_open_interest_interval(300_000)
    assert label == "5min"


def test_choose_open_interest_interval_daily():
    label, ms = choose_open_interest_interval(86_400_000)
    assert label == "1d"
