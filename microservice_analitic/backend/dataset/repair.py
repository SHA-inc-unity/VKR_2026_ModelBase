"""Dataset repair operations for the quality-audit feature.

Two orchestration handlers live here, both invoked from
:mod:`backend.data_client` in response to Kafka commands published by the
admin UI:

* :func:`load_ohlcv` — delegates exchange-aware OHLCV repair to
    ``cmd.data.dataset.repair_ohlcv`` in microservice_data. The data service
    owns market clients for Bybit/Binance/Kraken and performs the fetch +
    upsert while publishing the familiar repair progress events.
* :func:`recompute_features` — resolves the exchange-aware table name and
    proxies to ``cmd.data.dataset.compute_features`` so the data service
    recomputes the derived feature columns from current raw data.

The progress-event shape remains unchanged so the front-end can reuse the
same repair progress widget for all exchanges.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from modelline_shared.messaging.schemas import Envelope
from modelline_shared.messaging.topics import (
    CMD_DATA_DATASET_COMPUTE_FEATURES,
    CMD_DATA_DATASET_MAKE_TABLE,
        CMD_DATA_DATASET_REPAIR_OHLCV,
    EVT_ANALITIC_DATASET_REPAIR_PROGRESS,
)

_LOG = logging.getLogger(__name__)

# A "publisher" is anything we can call as ``send(topic, envelope)`` to
# emit a fire-and-forget event. The shared KafkaClient.send is the
# canonical implementation.
PublisherFn = Callable[[str, Envelope], Awaitable[None]]
RequestFn = Callable[..., Awaitable[dict[str, Any]]]

# Per-timeframe timeout (seconds) for cmd.data.dataset.compute_features.
# 1-minute bars produce millions of rows — SQL window functions can take
# well over 10 minutes for the densest table.
_RECOMPUTE_TIMEOUT: dict[str, float] = {
    "1m":  3_600.0,  # up to 1 hour for the highest-density timeframe
    "3m":  1_800.0,  # 30 min
    "5m":  1_800.0,  # 30 min
}
_RECOMPUTE_TIMEOUT_DEFAULT = 600.0   # 10 min for all sparser timeframes


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


# ── load_ohlcv handler ───────────────────────────────────────────────────────

async def load_ohlcv(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    start_ms: int,
    end_ms: int,
    correlation_id: str,
    request: RequestFn,
    publish: PublisherFn,
) -> dict[str, Any]:
    """Delegate OHLCV repair to microservice_data.

    The data service owns exchange-specific market clients and now performs
    the fetch + upsert internally, publishing the standard repair progress
    events with the original front-end correlation id.
    """
    try:
        return await request(
            CMD_DATA_DATASET_REPAIR_OHLCV,
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "exchange": exchange,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "progress_correlation_id": correlation_id,
            },
            timeout=600.0,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("load_ohlcv delegation failed")
        return {"error": f"repair_ohlcv failed: {exc}"}


# ── recompute_features handler ───────────────────────────────────────────────

async def recompute_features(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
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
            {"symbol": symbol, "timeframe": timeframe, "exchange": exchange},
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
