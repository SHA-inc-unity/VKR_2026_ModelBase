"""Synchronous Kafka data client for microservice_analitic.

All data access to the PostgreSQL database goes through microservice_data via
Kafka request/reply (cmd.data.*). This module exposes a simple synchronous API
that wraps the async KafkaClient in a long-lived background event loop so
that APScheduler jobs and WSGI/sync pipeline code can call it without
managing an asyncio event loop themselves.

Thread-safety: the background loop + KafkaClient are shared across all threads.
A threading.Lock guards the lazy initialisation; after that, all calls are
safe because asyncio.run_coroutine_threadsafe is thread-safe.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from modelline_shared.messaging.client import KafkaClient
from modelline_shared.messaging.topics import (
    CMD_ANALYTICS_HEALTH,
    CMD_ANALYTICS_MODEL_LIST,
    CMD_DATA_DATASET_COVERAGE,
    CMD_DATA_DATASET_INGEST,
    CMD_DATA_DATASET_LIST_TABLES,
    CMD_DATA_DATASET_MAKE_TABLE,
    CMD_DATA_DATASET_MISSING,
    CMD_DATA_DATASET_ROWS,
    CMD_DATA_DATASET_SCHEMA,
    CMD_DATA_DATASET_TIMESTAMPS,
    CMD_DATA_DB_PING,
)

_LOG = logging.getLogger(__name__)

# ── Background loop + client (lazy, shared) ──────────────────────────────────

_loop: asyncio.AbstractEventLoop | None = None
_client: KafkaClient | None = None
_init_lock = threading.Lock()

_DEFAULT_TIMEOUT = 30.0  # seconds — overridable per-call


async def _handle_health(_envelope) -> dict:
    return {"status": "ok", "service": "microservice_analitic", "version": "1.0.0"}


async def _handle_model_list(_envelope) -> dict:
    """Return the list of trained model versions from models/registry.json."""
    try:
        # Local import keeps Kafka-client bootstrap independent from the
        # (heavier) model package.
        from backend.model.config import MODELS_DIR
        from backend.model.report import load_registry

        models = load_registry(models_dir=MODELS_DIR, limit=1000)
        return {"models": models}
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("[data_client] model.list failed: %s", exc)
        return {"models": [], "error": str(exc)}


def _ensure_client() -> tuple[asyncio.AbstractEventLoop, KafkaClient]:
    """Return (loop, client), starting both lazily on first call."""
    global _loop, _client
    if _loop is not None and _client is not None:
        return _loop, _client
    with _init_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=loop.run_forever,
                daemon=True,
                name="data-client-loop",
            )
            t.start()
            _loop = loop
        if _client is None:
            client = KafkaClient(service_name="analitic")
            future = asyncio.run_coroutine_threadsafe(
                client.start(subscribe=[CMD_ANALYTICS_HEALTH, CMD_ANALYTICS_MODEL_LIST]), _loop
            )
            future.result(timeout=30)
            client.register_handler(CMD_ANALYTICS_HEALTH, _handle_health)
            client.register_handler(CMD_ANALYTICS_MODEL_LIST, _handle_model_list)
            _client = client
    return _loop, _client


def start() -> None:
    """Eagerly initialise the background Kafka client.

    Call this at service startup so incoming Kafka commands (e.g.
    ``cmd.analytics.health``) are handled even before the first outbound
    data request triggers the lazy ``_ensure_client()`` path.
    """
    _ensure_client()


def _request(topic: str, payload: dict[str, Any], timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Send a Kafka request and wait for the reply (blocking)."""
    loop, client = _ensure_client()
    coro = client.request(topic, payload, timeout=timeout)
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout + 5)  # +5s for asyncio overhead


# ── Public API ────────────────────────────────────────────────────────────────

def get_rows(
    table: str,
    start_ms: int,
    end_ms: int,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict]:
    """Return all rows in [start_ms, end_ms] as a list of dicts.

    Calls: cmd.data.dataset.rows
    """
    resp = _request(
        CMD_DATA_DATASET_ROWS,
        {"table": table, "start_ms": start_ms, "end_ms": end_ms},
        timeout=timeout,
    )
    return resp.get("rows", [])


def get_timestamps(
    table: str,
    start_ms: int,
    end_ms: int,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[int]:
    """Return sorted list of timestamps present in the table for [start_ms, end_ms].

    Calls: cmd.data.dataset.timestamps
    """
    resp = _request(
        CMD_DATA_DATASET_TIMESTAMPS,
        {"table": table, "start_ms": start_ms, "end_ms": end_ms},
        timeout=timeout,
    )
    return resp.get("timestamps", [])


def find_missing(
    table: str,
    start_ms: int,
    end_ms: int,
    step_ms: int,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[int]:
    """Return list of missing timestamps in [start_ms, end_ms] given step_ms.

    Calls: cmd.data.dataset.find_missing
    """
    resp = _request(
        CMD_DATA_DATASET_MISSING,
        {"table": table, "start_ms": start_ms, "end_ms": end_ms, "step_ms": step_ms},
        timeout=timeout,
    )
    return resp.get("missing", [])


def get_coverage(
    table: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict | None:
    """Return coverage metadata for the table, or ``None`` if it's empty/missing.

    Response shape from microservice_data (cmd.data.dataset.coverage):
        {"exists": false}  — table missing or empty
        {"exists": true, "rows": <int>, "min_ts_ms": <int>, "max_ts_ms": <int>}

    Calls: cmd.data.dataset.coverage
    """
    resp = _request(
        CMD_DATA_DATASET_COVERAGE,
        {"table": table},
        timeout=timeout,
    )
    if not resp or not resp.get("exists"):
        return None
    return {
        "rows":      resp.get("rows", 0),
        "min_ts_ms": resp.get("min_ts_ms"),
        "max_ts_ms": resp.get("max_ts_ms"),
    }


def list_tables(timeout: float = _DEFAULT_TIMEOUT) -> list[str]:
    """Return list of dataset table names managed by microservice_data.

    Calls: cmd.data.dataset.list_tables
    """
    resp = _request(CMD_DATA_DATASET_LIST_TABLES, {}, timeout=timeout)
    return resp.get("tables", [])


def get_schema(table: str, timeout: float = _DEFAULT_TIMEOUT) -> list[dict]:
    """Return column schema for the given table as a list of {name, type} dicts.

    Calls: cmd.data.dataset.table_schema
    """
    resp = _request(CMD_DATA_DATASET_SCHEMA, {"table": table}, timeout=timeout)
    return resp.get("schema", [])


def make_table_name(
    symbol: str,
    timeframe: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Return the canonical table name for (symbol, timeframe).

    Calls: cmd.data.dataset.make_table_name
    """
    resp = _request(
        CMD_DATA_DATASET_MAKE_TABLE,
        {"symbol": symbol, "timeframe": timeframe},
        timeout=timeout,
    )
    return resp["table_name"]


def ingest(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    timeout: float = 300.0,  # ingestion can take minutes
) -> dict:
    """Trigger microservice_data to ingest market data for the given range.

    Calls: cmd.data.dataset.ingest
    NOTE: cmd.data.dataset.ingest must be fully implemented in microservice_data.
    """
    resp = _request(
        CMD_DATA_DATASET_INGEST,
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
        timeout=timeout,
    )
    return resp


def db_ping(timeout: float = 5.0) -> bool:
    """Ping the data service DB and return True if healthy.

    Calls: cmd.data.db.ping
    """
    try:
        resp = _request(CMD_DATA_DB_PING, {}, timeout=timeout)
        return bool(resp.get("ok", False))
    except Exception as exc:
        _LOG.warning("[data_client] db_ping failed: %s", exc)
        return False
