from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib import error, parse, request

from .constants import (
    BYBIT_BASE_URL,
    INTERVAL_TO_STEP_MS,
    MAX_PARALLEL_API_WORKERS,
    MAX_RETRIES,
    OPEN_INTEREST_INTERVALS,
    PAGE_LIMIT_FUNDING,
    PAGE_LIMIT_KLINE,
    PAGE_LIMIT_OPEN_INTEREST,
    REQUEST_TIMEOUT_SECONDS,
)
from .timelog import now, tlog


def api_get_json(path: str, params: dict[str, object]) -> dict:
    """Запрашивает JSON у публичного API Bybit с повторами."""
    url = f"{BYBIT_BASE_URL}{path}?{parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    headers = {"Accept": "application/json", "User-Agent": "market-dataset-demo/1.0"}
    for attempt in range(1, MAX_RETRIES + 1):
        t_req = now()
        try:
            req = request.Request(url, headers=headers, method="GET")
            with request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                tlog.warning("api | HTTP %s %s attempt=%d, retry in %ds", exc.code, path, attempt, attempt)
                time.sleep(attempt)
                continue
            tlog.error("api | HTTP %s %s: %s", exc.code, path, body[:300])
            raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRIES:
                tlog.warning("api | %s %s attempt=%d, retry: %s", type(exc).__name__, path, attempt, exc)
                time.sleep(attempt)
                continue
            tlog.error("api | %s %s failed after %d attempts: %s", type(exc).__name__, path, MAX_RETRIES, exc)
            raise RuntimeError(f"Failed to fetch {path}: {exc}") from exc

        if payload.get("retCode") == 0:
            tlog.debug("api | GET %s attempt=%d elapsed=%.3fs", path, attempt, now() - t_req)
            return payload
        if attempt < MAX_RETRIES and payload.get("retCode") in {10000, 10006, 10016, 10018}:
            tlog.warning("api | retCode=%s %s attempt=%d, retry", payload.get("retCode"), path, attempt)
            time.sleep(attempt)
            continue
        tlog.error("api | retCode=%s retMsg=%s %s", payload.get("retCode"), payload.get("retMsg"), path)
        raise RuntimeError(
            f"Bybit API error for {path}: retCode={payload.get('retCode')}, retMsg={payload.get('retMsg')}"
        )
    tlog.error("api | exhausted %d retries for %s", MAX_RETRIES, path)
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
    """Скачивает исторические index_price свечи параллельными запросами.

    Bybit API максимально возвращает 1000 свечей за запрос.
    Диапазон делится на окна по 1000 свечей, которые загружаются
    параллельно через ThreadPoolExecutor (до MAX_PARALLEL_API_WORKERS
    одновременных запросов — хорошо укладывается в лимит 120 req/s по IP).
    """
    if start_ms > end_ms:
        return []

    callback_start = start_ms if progress_start_ms is None else progress_start_ms
    callback_end = end_ms if progress_end_ms is None else progress_end_ms

    # Шаг в мс по интервалу; для неизвестных интервалов — безопасный fallback 1m
    step_ms = INTERVAL_TO_STEP_MS.get(interval, 60_000)
    window_ms = PAGE_LIMIT_KLINE * step_ms

    # Предвычисляем все окна страниц без динамических зависимостей между ними
    windows: list[tuple[int, int]] = []
    t = end_ms
    while t >= start_ms:
        ws = max(start_ms, t - window_ms + step_ms)
        windows.append((ws, t))
        t = ws - step_ms

    t0 = now()
    tlog.info(
        "fetch_index_prices | START symbol=%s interval=%s windows=%d range=[%d,%d]",
        symbol, interval, len(windows), start_ms, end_ms,
    )
    rows: dict[int, float] = {}

    def _fetch_window(ws: int, we: int) -> dict[int, float]:
        t_w = now()
        try:
            payload = api_get_json(
                "/v5/market/index-price-kline",
                {
                    "category": category,
                    "symbol": symbol,
                    "interval": interval,
                    "start": ws,
                    "end": we,
                    "limit": PAGE_LIMIT_KLINE,
                },
            )
            partial: dict[int, float] = {}
            for item in payload.get("result", {}).get("list", []):
                timestamp_ms = int(item[0])
                if start_ms <= timestamp_ms <= end_ms:
                    partial[timestamp_ms] = float(item[4])
            tlog.debug(
                "fetch_index_prices | window ws=%d we=%d rows=%d elapsed=%.3fs",
                ws, we, len(partial), now() - t_w,
            )
            return partial
        except Exception:
            tlog.exception(
                "fetch_index_prices | window FAILED ws=%d we=%d elapsed=%.3fs",
                ws, we, now() - t_w,
            )
            raise

    _cb = progress_callback  # local ref so we can disable without touching outer scope
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_API_WORKERS, len(windows))) as executor:
        futures = [executor.submit(_fetch_window, ws, we) for ws, we in windows]
        for future in as_completed(futures):
            rows.update(future.result())
            if _cb is not None:
                try:
                    _cb(sum(1 for ts in rows if callback_start <= ts <= callback_end))
                except Exception:
                    tlog.warning(
                        "fetch_index_prices | progress_callback raised (non-fatal) — disabling"
                    )
                    _cb = None

    tlog.info(
        "fetch_index_prices | DONE symbol=%s interval=%s total_rows=%d elapsed=%.3fs",
        symbol, interval, len(rows), now() - t0,
    )
    return sorted(rows.items())


def fetch_funding_rates(category: str, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические funding rate точки."""
    t0 = now()
    pages = 0
    tlog.info("fetch_funding_rates | START symbol=%s range=[%d,%d]", symbol, start_ms, end_ms)
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
        pages += 1
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
    tlog.info("fetch_funding_rates | DONE symbol=%s rows=%d pages=%d elapsed=%.3fs", symbol, len(rows), pages, now() - t0)
    return sorted(rows.items())


def fetch_open_interest(category: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Скачивает исторические точки open interest параллельными запросами.

    Диапазон делится на временные окна по PAGE_LIMIT_OPEN_INTEREST точек.
    Каждое окно скачивается независимо через ThreadPoolExecutor —
    это даёт 8-10x ускорение по сравнению с последовательной курсорной
    пагинацией (220 окон × 10 workers ≈ 9s вместо 92s).
    """
    t0 = now()
    if start_ms > end_ms:
        tlog.info("fetch_open_interest | START symbol=%s interval=%s range=[%d,%d]", symbol, interval, start_ms, end_ms)
        tlog.info("fetch_open_interest | DONE symbol=%s rows=0 pages=0 elapsed=%.3fs", symbol, now() - t0)
        return []

    # Step size for the requested OI interval (fallback to 1h)
    oi_step_ms = next(
        (step for name, step in OPEN_INTEREST_INTERVALS if name == interval),
        3_600_000,
    )
    window_ms = PAGE_LIMIT_OPEN_INTEREST * oi_step_ms

    # Pre-compute non-overlapping time windows (newest first, same pattern as fetch_index_prices)
    windows: list[tuple[int, int]] = []
    t = end_ms
    while t >= start_ms:
        ws = max(start_ms, t - window_ms + oi_step_ms)
        windows.append((ws, t))
        t = ws - oi_step_ms

    tlog.info(
        "fetch_open_interest | START symbol=%s interval=%s windows=%d range=[%d,%d]",
        symbol, interval, len(windows), start_ms, end_ms,
    )
    rows: dict[int, float] = {}
    total_pages = 0

    def _fetch_oi_window(ws: int, we: int) -> tuple[dict[int, float], int]:
        t_w = now()
        partial: dict[int, float] = {}
        pages = 0
        cursor: str | None = None
        seen: set[str] = set()
        try:
            while True:
                params: dict[str, object] = {
                    "category": category,
                    "symbol": symbol,
                    "intervalTime": interval,
                    "startTime": ws,
                    "endTime": we,
                    "limit": PAGE_LIMIT_OPEN_INTEREST,
                }
                if cursor:
                    params["cursor"] = cursor
                payload = api_get_json("/v5/market/open-interest", params)
                pages += 1
                result = payload.get("result", {})
                items = result.get("list", [])
                if not items:
                    break
                for item in items:
                    timestamp_ms = int(item["timestamp"])
                    if ws <= timestamp_ms <= we:
                        partial[timestamp_ms] = float(item["openInterest"])
                # Окно предварительно ограничено PAGE_LIMIT_OPEN_INTEREST интервалами,
                # поэтому первая страница заполненного окна уже даёт все точки —
                # следовать курсору нет смысла.
                cursor = result.get("nextPageCursor") or None
                if not cursor or cursor in seen or len(partial) >= PAGE_LIMIT_OPEN_INTEREST:
                    break
                seen.add(cursor)
            tlog.debug(
                "fetch_open_interest | window ws=%d we=%d rows=%d pages=%d elapsed=%.3fs",
                ws, we, len(partial), pages, now() - t_w,
            )
            return partial, pages
        except Exception:
            tlog.exception(
                "fetch_open_interest | window FAILED ws=%d we=%d elapsed=%.3fs",
                ws, we, now() - t_w,
            )
            raise

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_API_WORKERS, len(windows))) as executor:
        futures = [executor.submit(_fetch_oi_window, ws, we) for ws, we in windows]
        for future in as_completed(futures):
            partial, pg = future.result()
            rows.update(partial)
            total_pages += pg

    tlog.info(
        "fetch_open_interest | DONE symbol=%s rows=%d pages=%d elapsed=%.3fs",
        symbol, len(rows), total_pages, now() - t0,
    )
    return sorted(rows.items())
