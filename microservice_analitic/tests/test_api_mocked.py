"""Tests for backend.dataset.api — all functions with mocked urllib."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib import error as urllib_error

import pytest

from backend.dataset.api import (
    api_get_json,
    fetch_funding_rates,
    fetch_index_prices,
    fetch_instrument_details,
    fetch_open_interest,
)


def _mock_response(payload: dict):
    """Returns a MagicMock context-manager that yields a JSON response."""
    r = MagicMock()
    r.read.return_value = json.dumps(payload).encode("utf-8")
    r.__enter__ = MagicMock(return_value=r)
    r.__exit__ = MagicMock(return_value=False)
    return r


# ---------------------------------------------------------------------------
# api_get_json
# ---------------------------------------------------------------------------

def test_api_get_json_success():
    payload = {"retCode": 0, "result": {"list": []}}
    with patch("backend.dataset.api.request.urlopen", return_value=_mock_response(payload)):
        result = api_get_json("/v5/test", {"symbol": "BTCUSDT"})
    assert result["retCode"] == 0


def test_api_get_json_retryable_http_error_then_success():
    """Covers the retry branch for 429/503 HTTP errors."""
    payload = {"retCode": 0, "result": {}}
    http_err = urllib_error.HTTPError(
        url="http://x", code=429, msg="Too Many Requests",
        hdrs=None, fp=None,
    )
    responses = [http_err, _mock_response(payload)]

    def side_effect(*args, **kwargs):
        val = responses.pop(0)
        if isinstance(val, Exception):
            raise val
        return val

    with patch("backend.dataset.api.request.urlopen", side_effect=side_effect):
        with patch("backend.dataset.api.time.sleep"):  # don't actually sleep
            result = api_get_json("/v5/test", {})
    assert result["retCode"] == 0


def test_api_get_json_http_error_non_retryable_raises():
    http_err = urllib_error.HTTPError(
        url="http://x", code=404, msg="Not Found",
        hdrs=None, fp=MagicMock(read=MagicMock(return_value=b"not found")),
    )
    with patch("backend.dataset.api.request.urlopen", side_effect=http_err):
        with pytest.raises(RuntimeError, match="HTTP 404"):
            api_get_json("/v5/test", {})


def test_api_get_json_url_error_retries_then_raises():
    err = urllib_error.URLError(reason="connection refused")
    with patch("backend.dataset.api.request.urlopen", side_effect=err):
        with patch("backend.dataset.api.time.sleep"):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                api_get_json("/v5/test", {})


def test_api_get_json_retcode_error_raises():
    payload = {"retCode": 10001, "retMsg": "bad request"}
    with patch("backend.dataset.api.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="Bybit API error"):
            api_get_json("/v5/test", {})


def test_api_get_json_retryable_retcode_then_success():
    """Covers retryable retCode branch (10006 etc)."""
    payload_err = {"retCode": 10006, "retMsg": "rate limit"}
    payload_ok = {"retCode": 0, "result": {}}
    responses = [_mock_response(payload_err), _mock_response(payload_ok)]

    def side_effect(*args, **kwargs):
        return responses.pop(0)

    with patch("backend.dataset.api.request.urlopen", side_effect=side_effect):
        with patch("backend.dataset.api.time.sleep"):
            result = api_get_json("/v5/test", {})
    assert result["retCode"] == 0


def test_api_get_json_exhausted_retries():
    """Covers exhausted retries path."""
    payload_err = {"retCode": 10006, "retMsg": "rate limit"}

    with patch("backend.dataset.api.request.urlopen", return_value=_mock_response(payload_err)):
        with patch("backend.dataset.api.time.sleep"):
            with pytest.raises(RuntimeError):
                api_get_json("/v5/test", {})


# ---------------------------------------------------------------------------
# fetch_instrument_details
# ---------------------------------------------------------------------------

def test_fetch_instrument_details_success():
    payload = {
        "retCode": 0,
        "result": {
            "list": [{"launchTime": "1609459200000", "fundingInterval": 480}]
        },
    }
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        launch_ms, funding_ms = fetch_instrument_details("linear", "BTCUSDT")
    assert launch_ms == 1_609_459_200_000
    assert funding_ms == 480 * 60_000


def test_fetch_instrument_details_not_found_raises():
    payload = {"retCode": 0, "result": {"list": []}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        with pytest.raises(RuntimeError, match="Instrument not found"):
            fetch_instrument_details("linear", "BTCUSDT")


def test_fetch_instrument_details_zero_funding_uses_default():
    payload = {
        "retCode": 0,
        "result": {"list": [{"launchTime": "0", "fundingInterval": 0}]},
    }
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        _, funding_ms = fetch_instrument_details("linear", "BTCUSDT")
    assert funding_ms == 28_800_000  # default


# ---------------------------------------------------------------------------
# fetch_index_prices
# ---------------------------------------------------------------------------

def test_fetch_index_prices_success():
    items = [[str(1_704_067_200_000 + i * 3_600_000), 0, 0, 0, str(40000 + i)] for i in range(3)]
    payload = {"retCode": 0, "result": {"list": items}}
    start = 1_704_067_200_000
    end = start + 2 * 3_600_000

    with patch("backend.dataset.api.api_get_json", return_value=payload):
        rows = fetch_index_prices("linear", "BTCUSDT", "60", start, end)
    assert len(rows) == 3
    assert rows[0][1] == 40000.0


def test_fetch_index_prices_empty_returns_empty():
    payload = {"retCode": 0, "result": {"list": []}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        rows = fetch_index_prices("linear", "BTCUSDT", "60", 0, 1_000_000)
    assert rows == []


def test_fetch_index_prices_with_progress_callback():
    items = [[str(1_704_067_200_000), 0, 0, 0, "40000"]]
    payload = {"retCode": 0, "result": {"list": items}}
    progress_calls = []
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        fetch_index_prices(
            "linear", "BTCUSDT", "60",
            1_704_067_200_000, 1_704_067_200_000,
            progress_callback=lambda n: progress_calls.append(n),
        )
    assert len(progress_calls) > 0


# ---------------------------------------------------------------------------
# fetch_funding_rates
# ---------------------------------------------------------------------------

def test_fetch_funding_rates_success():
    ts_ms = 1_704_067_200_000
    items = [{"fundingRateTimestamp": str(ts_ms), "fundingRate": "0.0001"}]
    payload = {"retCode": 0, "result": {"list": items}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        # start_ms == end_ms == ts_ms → oldest == start_ms → loop breaks after one call
        rows = fetch_funding_rates("linear", "BTCUSDT", ts_ms, ts_ms)
    assert rows[0][1] == pytest.approx(0.0001)


def test_fetch_funding_rates_empty():
    payload = {"retCode": 0, "result": {"list": []}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        rows = fetch_funding_rates("linear", "BTCUSDT", 0, 1_000_000_000)
    assert rows == []


# ---------------------------------------------------------------------------
# fetch_open_interest
# ---------------------------------------------------------------------------

def test_fetch_open_interest_success():
    items = [{"timestamp": "1704067200000", "openInterest": "12345.67"}]
    payload = {"retCode": 0, "result": {"list": items, "nextPageCursor": ""}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        rows = fetch_open_interest("linear", "BTCUSDT", "1h", 0, 2_000_000_000_000)
    assert rows[0][1] == 12345.67


def test_fetch_open_interest_pagination():
    """Covers the cursor pagination path."""
    items = [{"timestamp": "1704067200000", "openInterest": "1000"}]
    first = {"retCode": 0, "result": {"list": items, "nextPageCursor": "cursor123"}}
    second = {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}}
    responses = [first, second]

    with patch("backend.dataset.api.api_get_json", side_effect=lambda *a, **k: responses.pop(0)):
        rows = fetch_open_interest("linear", "BTCUSDT", "1h", 0, 2_000_000_000_000)
    assert len(rows) == 1


def test_fetch_open_interest_empty():
    payload = {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}}
    with patch("backend.dataset.api.api_get_json", return_value=payload):
        rows = fetch_open_interest("linear", "BTCUSDT", "1h", 0, 1_000_000_000)
    assert rows == []
