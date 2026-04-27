"""Dataset repair operations for the quality-audit feature.

Two orchestration handlers live here, both invoked from
:mod:`backend.data_client` in response to Kafka commands published by the
admin UI:

* :func:`load_ohlcv` — fetches OHLCV klines from Bybit
  (``/v5/market/kline``) and persists them via
  ``cmd.data.dataset.upsert_ohlcv`` (preserves all non-OHLCV columns).
* :func:`recompute_features` — proxies to
  ``cmd.data.dataset.compute_features`` so the data service recomputes the
  derived feature columns from current raw data.

Both functions publish ``events.analitic.dataset.repair.progress`` events
with ``correlation_id`` so the front-end can render a live progress
component identical in shape to the existing :class:`IngestProgress`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Awaitable, Callable

from modelline_shared.messaging.schemas import Envelope
from modelline_shared.messaging.topics import (
    CMD_DATA_DATASET_COMPUTE_FEATURES,
    CMD_DATA_DATASET_MAKE_TABLE,
    CMD_DATA_DATASET_UPSERT_OHLCV,
    EVT_ANALITIC_DATASET_REPAIR_PROGRESS,
)

from .api import api_get_json
from .constants import (
    INTERVAL_TO_STEP_MS,
    MAX_PARALLEL_API_WORKERS,
    PAGE_LIMIT_KLINE,
    TIMEFRAMES,
)

_LOG = logging.getLogger(__name__)

# A "publisher" is anything we can call as ``send(topic, envelope)`` to
# emit a fire-and-forget event. The shared KafkaClient.send is the
# canonical implementation.
PublisherFn = Callable[[str, Envelope], Awaitable[None]]
RequestFn = Callable[..., Awaitable[dict[str, Any]]]

# Default exchange — the admin UI supports only Bybit today.
_DEFAULT_EXCHANGE = "bybit"

# aiokafka's default max.message.bytes is 1 MB.  Targeting ≤ 700 KB per
# message at ~150 bytes/row (JSON) gives a safe ceiling of ~4 500 rows.
_UPSERT_BATCH_SIZE = 4_500

# Per-timeframe timeout (seconds) for cmd.data.dataset.compute_features.
# 1-minute bars produce millions of rows — SQL window functions can take
# well over 10 minutes for the densest table.
_RECOMPUTE_TIMEOUT: dict[str, float] = {
    "1m":  3_600.0,  # up to 1 hour for the highest-density timeframe
    "3m":  1_800.0,  # 30 min
    "5m":  1_800.0,  # 30 min
}
_RECOMPUTE_TIMEOUT_DEFAULT = 600.0   # 10 min for all sparser timeframes

# Bybit kline interval is the same string used for index-price klines.
_TIMEFRAME_TO_BYBIT_INTERVAL: dict[str, str] = {
    label: bybit for label, (bybit, _step) in TIMEFRAMES.items()
}


# ── Progress emission ────────────────────────────────────────────────────────

async def _emit_progress(
    publish: PublisherFn,
    correlation_id: str,
    *,
    stage: str,
    label: str,
    status: str,        # "running" | "done" | "error"
    progress: int,      # 0..100
    detail: str | None = None,
) -> None:
    """Fire one progress event. Errors are swallowed (best-effort)."""
    try:
        env = Envelope(
            correlation_id=correlation_id,
            type="dataset.repair.progress",
            payload={
                "correlation_id": correlation_id,
                "stage":          stage,
                "label":          label,
                "status":         status,
                "progress":       progress,
                "detail":         detail,
            },
        )
        await publish(EVT_ANALITIC_DATASET_REPAIR_PROGRESS, env)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("repair | progress emit failed (non-fatal): %s", exc)


# ── Bybit kline fetch (parallel, mirrors fetch_close_prices) ─────────────────

def _fetch_klines_parallel(
    category: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[tuple[int, float, float, float, float, float, float]]:
    """Download OHLCV klines for the inclusive [start_ms, end_ms] range.

    Returns a list of tuples ``(ts_ms, open, high, low, close, volume,
    turnover)`` sorted ascending by timestamp.

    Bybit's ``/v5/market/kline`` returns at most 1000 candles per request,
    so the range is split into windows and fetched in parallel through a
    thread pool. ``progress_callback(done_pages, total_pages)`` is fired
    after each completed page (best-effort, errors disable it).
    """
    if start_ms > end_ms:
        return []

    step_ms = INTERVAL_TO_STEP_MS.get(interval, 60_000)
    window_ms = PAGE_LIMIT_KLINE * step_ms

    windows: list[tuple[int, int]] = []
    t = end_ms
    while t >= start_ms:
        ws = max(start_ms, t - window_ms + step_ms)
        windows.append((ws, t))
        t = ws - step_ms

    rows: dict[int, tuple[float, float, float, float, float, float]] = {}

    def _fetch_window(ws: int, we: int) -> dict[int, tuple[float, float, float, float, float, float]]:
        payload = api_get_json(
            "/v5/market/kline",
            {
                "category": category,
                "symbol":   symbol,
                "interval": interval,
                "start":    ws,
                "end":      we,
                "limit":    PAGE_LIMIT_KLINE,
            },
        )
        partial: dict[int, tuple[float, float, float, float, float, float]] = {}
        # Bybit kline list shape: [start, open, high, low, close, volume, turnover].
        # Phase-4 candle-source-of-truth: every persisted candle must carry
        # all four prices sourced from the same kline; we therefore pass
        # close through to the data service alongside O/H/L.
        for item in payload.get("result", {}).get("list", []):
            ts_ms = int(item[0])
            if start_ms <= ts_ms <= end_ms:
                partial[ts_ms] = (
                    float(item[1]),  # open
                    float(item[2]),  # high
                    float(item[3]),  # low
                    float(item[4]),  # close
                    float(item[5]),  # volume
                    float(item[6]),  # turnover
                )
        return partial

    total_pages = len(windows)
    done_pages = 0
    cb = progress_callback
    with ThreadPoolExecutor(
        max_workers=min(MAX_PARALLEL_API_WORKERS, max(1, total_pages))
    ) as executor:
        futures = [executor.submit(_fetch_window, ws, we) for ws, we in windows]
        for fut in as_completed(futures):
            rows.update(fut.result())
            done_pages += 1
            if cb is not None:
                try:
                    cb(done_pages, total_pages)
                except Exception:  # noqa: BLE001
                    cb = None  # disable on first failure

    out: list[tuple[int, float, float, float, float, float, float]] = [
        (ts, *vals) for ts, vals in sorted(rows.items())
    ]
    return out


# ── load_ohlcv handler ───────────────────────────────────────────────────────

async def load_ohlcv(
    *,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    correlation_id: str,
    request: RequestFn,
    publish: PublisherFn,
) -> dict[str, Any]:
    """Fetch OHLCV klines from Bybit and persist them via the data service.

    The six raw OHLCV columns (open_price/high_price/low_price/close_price/
    volume/turnover) are written into the existing market table without
    overwriting any other column (funding_rate, open_interest, rsi, derived
    features). Phase-4 candle-source-of-truth: ``close_price`` is sent
    alongside O/H/L so the persisted candle remains a single tuple. See
    :meth:`DatasetRepository.BulkUpdateOhlcvAsync` for the SQL contract.
    """
    t0 = time.time()
    interval = _TIMEFRAME_TO_BYBIT_INTERVAL.get(timeframe)
    if interval is None:
        return {"error": f"unsupported timeframe: {timeframe}"}

    # Stage 1: prepare — resolve the canonical table name.
    await _emit_progress(
        publish, correlation_id,
        stage="prepare", label="Подготовка",
        status="running", progress=0,
    )
    try:
        mt = await request(
            CMD_DATA_DATASET_MAKE_TABLE,
            {"symbol": symbol, "timeframe": timeframe},
        )
    except Exception as exc:  # noqa: BLE001
        await _emit_progress(
            publish, correlation_id,
            stage="prepare", label="Подготовка",
            status="error", progress=0, detail=str(exc),
        )
        return {"error": f"make_table_name failed: {exc}"}
    table = mt.get("table") or mt.get("table_name")
    if not table:
        await _emit_progress(
            publish, correlation_id,
            stage="prepare", label="Подготовка",
            status="error", progress=0, detail="empty table name",
        )
        return {"error": "data service returned empty table name"}
    await _emit_progress(
        publish, correlation_id,
        stage="prepare", label="Подготовка",
        status="done", progress=100, detail=table,
    )

    # Stage 2: fetch — parallel pages from Bybit.
    await _emit_progress(
        publish, correlation_id,
        stage="fetch", label="Загрузка свечей",
        status="running", progress=0,
    )
    loop = asyncio.get_running_loop()

    # The fetcher is sync (uses urllib + ThreadPoolExecutor inside); push it
    # to a worker thread so the asyncio loop stays free.
    last_emit = 0.0
    pending_progress: list[tuple[int, int]] = []

    def _on_page(done: int, total: int) -> None:
        # Called from a worker thread; we just stash the latest counters
        # for the asyncio task to drain. Guarded by GIL — list.append is
        # atomic enough for this best-effort progress channel.
        pending_progress.append((done, total))

    fetch_task = loop.run_in_executor(
        None,
        _fetch_klines_parallel,
        "linear", symbol, interval, start_ms, end_ms, _on_page,
    )

    # Drain the progress queue while the fetch is running.
    while not fetch_task.done():
        await asyncio.sleep(0.25)
        if pending_progress:
            done, total = pending_progress[-1]
            pending_progress.clear()
            now = time.time()
            if now - last_emit >= 0.4 and total > 0:
                await _emit_progress(
                    publish, correlation_id,
                    stage="fetch", label="Загрузка свечей",
                    status="running",
                    progress=min(99, int(done * 100 / total)),
                    detail=f"{done}/{total} страниц",
                )
                last_emit = now

    try:
        rows = await fetch_task
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("load_ohlcv | fetch failed")
        await _emit_progress(
            publish, correlation_id,
            stage="fetch", label="Загрузка свечей",
            status="error", progress=0, detail=str(exc),
        )
        return {"error": f"fetch failed: {exc}"}

    await _emit_progress(
        publish, correlation_id,
        stage="fetch", label="Загрузка свечей",
        status="done", progress=100,
        detail=f"{len(rows):,} свечей",
    )

    if not rows:
        await _emit_progress(
            publish, correlation_id,
            stage="upsert", label="Запись в базу",
            status="done", progress=100, detail="нет данных",
        )
        return {
            "table":         table,
            "rows_fetched":  0,
            "rows_affected": 0,
            "elapsed_sec":   round(time.time() - t0, 2),
        }

    # Stage 3: upsert via the data service — split into batches so that
    # each Kafka message stays well below the 1 MB aiokafka limit.
    # (_UPSERT_BATCH_SIZE rows × ~150 bytes/row ≈ 675 KB ≤ 700 KB target.)
    total_rows   = len(rows)
    total_batches = (total_rows + _UPSERT_BATCH_SIZE - 1) // _UPSERT_BATCH_SIZE
    await _emit_progress(
        publish, correlation_id,
        stage="upsert", label="Запись в базу",
        status="running", progress=0,
        detail=f"{total_rows:,} строк",
    )
    total_affected = 0
    sent_rows = 0
    for batch_idx in range(total_batches):
        batch = rows[batch_idx * _UPSERT_BATCH_SIZE : (batch_idx + 1) * _UPSERT_BATCH_SIZE]
        batch_payload = {
            "table":     table,
            "symbol":    symbol,
            "exchange":  _DEFAULT_EXCHANGE,
            "timeframe": timeframe,
            "rows": [
                {
                    "ts_ms":    ts,
                    "open":     o,
                    "high":     h,
                    "low":      lo,
                    "close":    c,
                    "volume":   v,
                    "turnover": tu,
                }
                for ts, o, h, lo, c, v, tu in batch
            ],
        }
        # 5 min minimum per batch; +1 s per 5 000 rows in the batch.
        batch_timeout = max(300.0, len(batch) / 5_000.0)
        try:
            reply = await request(
                CMD_DATA_DATASET_UPSERT_OHLCV, batch_payload, timeout=batch_timeout,
            )
        except asyncio.TimeoutError:
            await _emit_progress(
                publish, correlation_id,
                stage="upsert", label="Запись в базу",
                status="error", progress=0,
                detail=f"timeout on batch {batch_idx + 1}/{total_batches}",
            )
            return {"error": f"upsert_ohlcv timeout (batch {batch_idx + 1})"}
        except Exception as exc:  # noqa: BLE001
            await _emit_progress(
                publish, correlation_id,
                stage="upsert", label="Запись в базу",
                status="error", progress=0, detail=str(exc),
            )
            return {"error": f"upsert_ohlcv failed: {exc}"}

        if "error" in reply:
            await _emit_progress(
                publish, correlation_id,
                stage="upsert", label="Запись в базу",
                status="error", progress=0, detail=reply["error"],
            )
            return {"error": reply["error"]}

        total_affected += int(reply.get("rows_affected", 0) or 0)
        sent_rows += len(batch)
        # Cumulative progress (capped at 99 until final done event).
        await _emit_progress(
            publish, correlation_id,
            stage="upsert", label="Запись в базу",
            status="running",
            progress=min(99, int(sent_rows * 100 / total_rows)),
            detail=f"{sent_rows:,}/{total_rows:,} строк",
        )

    await _emit_progress(
        publish, correlation_id,
        stage="upsert", label="Запись в базу",
        status="done", progress=100,
        detail=f"{total_affected:,} обновлено",
    )
    return {
        "table":         table,
        "rows_fetched":  total_rows,
        "rows_affected": total_affected,
        "elapsed_sec":   round(time.time() - t0, 2),
    }


# ── recompute_features handler ───────────────────────────────────────────────

async def recompute_features(
    *,
    symbol: str,
    timeframe: str,
    correlation_id: str,
    request: RequestFn,
    publish: PublisherFn,
) -> dict[str, Any]:
    """Re-derive OHLCV/RSI feature columns by delegating to the data service.

    All heavy SQL lives on the data side; this is a thin orchestrator that
    publishes progress events around the synchronous request/reply.
    """
    t0 = time.time()

    await _emit_progress(
        publish, correlation_id,
        stage="prepare", label="Подготовка",
        status="running", progress=0,
    )
    try:
        mt = await request(
            CMD_DATA_DATASET_MAKE_TABLE,
            {"symbol": symbol, "timeframe": timeframe},
        )
    except Exception as exc:  # noqa: BLE001
        await _emit_progress(
            publish, correlation_id,
            stage="prepare", label="Подготовка",
            status="error", progress=0, detail=str(exc),
        )
        return {"error": f"make_table_name failed: {exc}"}
    table = mt.get("table") or mt.get("table_name")
    if not table:
        await _emit_progress(
            publish, correlation_id,
            stage="prepare", label="Подготовка",
            status="error", progress=0, detail="empty table name",
        )
        return {"error": "data service returned empty table name"}
    await _emit_progress(
        publish, correlation_id,
        stage="prepare", label="Подготовка",
        status="done", progress=100, detail=table,
    )

    await _emit_progress(
        publish, correlation_id,
        stage="recompute", label="Пересчёт фич",
        status="running", progress=0,
    )
    recompute_timeout = _RECOMPUTE_TIMEOUT.get(timeframe, _RECOMPUTE_TIMEOUT_DEFAULT)
    try:
        reply = await request(
            CMD_DATA_DATASET_COMPUTE_FEATURES,
            {"table": table},
            timeout=recompute_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        await _emit_progress(
            publish, correlation_id,
            stage="recompute", label="Пересчёт фич",
            status="error", progress=0, detail=str(exc),
        )
        return {"error": f"compute_features failed: {exc}"}

    if "error" in reply:
        await _emit_progress(
            publish, correlation_id,
            stage="recompute", label="Пересчёт фич",
            status="error", progress=0, detail=reply["error"],
        )
        return {"error": reply["error"]}

    rows_updated = reply.get("rows_updated") or reply.get("rows_affected") or 0
    await _emit_progress(
        publish, correlation_id,
        stage="recompute", label="Пересчёт фич",
        status="done", progress=100,
        detail=f"{int(rows_updated):,} строк",
    )
    return {
        "table":        table,
        "rows_updated": int(rows_updated),
        "elapsed_sec":  round(time.time() - t0, 2),
    }
