from __future__ import annotations

import json
import time
from urllib import error, parse, request

from .constants import (
    BYBIT_BASE_URL,
    MAX_RETRIES,
    PAGE_LIMIT_FUNDING,
    PAGE_LIMIT_KLINE,
    PAGE_LIMIT_OPEN_INTEREST,
    REQUEST_TIMEOUT_SECONDS,
)


def api_get_json(path: str, params: dict[str, object]) -> dict:
    """Запрашивает JSON у публичного API Bybit с повторами."""
    url = f"{BYBIT_BASE_URL}{path}?{parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    headers = {"Accept": "application/json", "User-Agent": "market-dataset-demo/1.0"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRIES:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"Failed to fetch {path}: {exc}") from exc

        if payload.get("retCode") == 0:
            return payload
        if attempt < MAX_RETRIES and payload.get("retCode") in {10000, 10006, 10016, 10018}:
            time.sleep(attempt)
            continue
        raise RuntimeError(
            f"Bybit API error for {path}: retCode={payload.get('retCode')}, retMsg={payload.get('retMsg')}"
        )
    raise RuntimeError(f"Exhausted retries for {path}")


def fetch_instrument_details(category: str, symbol: str) -> tuple[int, int]:
    """Получает launchTime и fundingInterval по инструменту."""
    payload = api_get_json(
        "/v5/market/instruments-info",
        {"category": category, "symbol": symbol, "limit": 1},
    )
    items = payload.get("result", {}).get("list", [])
    if not items:
        raise RuntimeError(f"Instrument not found for {category}:{symbol}")
    item = items[0]
    launch_time_ms = int(item.get("launchTime", "0") or 0)
    funding_interval_ms = int(item.get("fundingInterval", 0) or 0) * 60_000
    return launch_time_ms, funding_interval_ms or 28_800_000


def fetch_index_prices(
    category: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    progress_callback=None,
    progress_start_ms: int | None = None,
    progress_end_ms: int | None = None,
) -> list[tuple[int, float]]:
    """Скачивает исторические index_price свечи и сообщает частичный прогресс."""
    rows = {}
    current_end = end_ms
    callback_start = start_ms if progress_start_ms is None else progress_start_ms
    callback_end = end_ms if progress_end_ms is None else progress_end_ms
    while current_end >= start_ms:
        payload = api_get_json(
            "/v5/market/index-price-kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "start": start_ms,
                "end": current_end,
                "limit": PAGE_LIMIT_KLINE,
            },
        )
        items = payload.get("result", {}).get("list", [])
        if not items:
            break
        oldest = None
        for item in items:
            timestamp_ms = int(item[0])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item[4])
                oldest = timestamp_ms if oldest is None else min(oldest, timestamp_ms)
        if progress_callback is not None:
            progress_callback(sum(1 for timestamp in rows if callback_start <= timestamp <= callback_end))
        if oldest is None or oldest <= start_ms:
            break
        current_end = oldest - 1
    return sorted(rows.items())


def fetch_funding_rates(category: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические funding rate точки."""
    rows = {}
    current_end = end_ms
    while current_end >= start_ms:
        payload = api_get_json(
            "/v5/market/funding/history",
            {
                "category": category,
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": current_end,
                "limit": PAGE_LIMIT_FUNDING,
            },
        )
        items = payload.get("result", {}).get("list", [])
        if not items:
            break
        oldest = None
        for item in items:
            timestamp_ms = int(item["fundingRateTimestamp"])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item["fundingRate"])
                oldest = timestamp_ms if oldest is None else min(oldest, timestamp_ms)
        if oldest is None or oldest <= start_ms:
            break
        current_end = oldest - 1
    return sorted(rows.items())


def fetch_open_interest(category: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические точки open interest."""
    rows = {}
    cursor = None
    seen = set()
    while True:
        payload = api_get_json(
            "/v5/market/open-interest",
            {
                "category": category,
                "symbol": symbol,
                "intervalTime": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": PAGE_LIMIT_OPEN_INTEREST,
                "cursor": cursor,
            },
        )
        result = payload.get("result", {})
        items = result.get("list", [])
        if not items:
            break
        for item in items:
            timestamp_ms = int(item["timestamp"])
            if start_ms <= timestamp_ms <= end_ms:
                rows[timestamp_ms] = float(item["openInterest"])
        cursor = result.get("nextPageCursor") or None
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return sorted(rows.items())
