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
import gc
import logging
import threading
from typing import Any

from modelline_shared.messaging.client import KafkaClient
from modelline_shared.messaging.topics import (
    CMD_ANALITIC_ANOMALY_DBSCAN,
    CMD_ANALITIC_ANOMALY_ISOLATION_FOREST,
    CMD_ANALITIC_DATASET_DISTRIBUTION,
    CMD_ANALITIC_DATASET_LOAD,
    CMD_ANALITIC_DATASET_LOAD_OHLCV,
    CMD_ANALITIC_DATASET_QUALITY_CHECK,
    CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
    CMD_ANALITIC_DATASET_STATUS,
    CMD_ANALITIC_DATASET_UNLOAD,
    CMD_ANALYTICS_HEALTH,
    CMD_ANALYTICS_MODEL_LIST,
    CMD_DATA_DATASET_COVERAGE,
    CMD_DATA_DATASET_EXPORT,
    CMD_DATA_DATASET_EXPORT_FULL,
    CMD_DATA_DATASET_INGEST,
    CMD_DATA_DATASET_LIST_TABLES,
    CMD_DATA_DATASET_MAKE_TABLE,
    CMD_DATA_DATASET_MISSING,
    CMD_DATA_DATASET_ROWS,
    CMD_DATA_DATASET_SCHEMA,
    CMD_DATA_DATASET_TIMESTAMPS,
    CMD_DATA_DB_PING,
)

from backend.anomaly.session import (
    MAX_SESSION_ROWS,
    SESSION_DIR,
    get_session,
    read_parquet_bounded,
    reset_session_dir,
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


# ── Anomaly / dataset session handlers ────────────────────────────────────

async def _handle_dataset_status(_envelope) -> dict:
    """Return current session metadata or ``{loaded: false}``."""
    meta = get_session().get_metadata()
    if meta is None:
        return {"loaded": False}
    return {"loaded": True, **meta}


async def _handle_dataset_unload(_envelope) -> dict:
    """Drop the active session (also deletes the parquet file)."""
    cleared = get_session().clear()
    return {"cleared": cleared}


async def _handle_dataset_load(envelope) -> dict:
    """Stream a dataset from microservice_data into a local Parquet file.

    Pipeline (memory-bounded):
        1. Ask data service for a CSV export — it returns a presigned MinIO URL.
        2. ``httpx`` downloads the CSV, streamed to a temp file (~1 MB chunks).
        3. ``pandas.read_csv(chunksize=50_000)`` parses chunks; each chunk is
           cast ``float64 → float32`` (except ``timestamp_utc``) and appended
           to a Parquet file via ``pyarrow.ParquetWriter``.
        4. Temporary CSV is deleted; only the Parquet stays on disk.

    The active session is replaced atomically; the previous parquet is
    deleted on success.
    """
    # Local imports — these are heavy and only needed when a load happens.
    import httpx
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    payload = envelope.payload or {}
    symbol    = payload.get("symbol")
    timeframe = payload.get("timeframe")
    if not symbol or not timeframe:
        return {"error": "missing required fields: symbol, timeframe"}

    # Single composite call: DataService resolves the table name, validates
    # existence + non-emptiness + size cap, and hands back a presigned MinIO
    # URL — all in one Kafka round-trip. The previous make_table → coverage
    # → export sequence is gone (3× the latency for no extra information).
    export = await _client_obj().request(
        CMD_DATA_DATASET_EXPORT_FULL,
        {
            "symbol":    symbol,
            "timeframe": timeframe,
            "max_rows":  MAX_SESSION_ROWS,
        },
        timeout=300.0,
    )
    if "error" in export:
        # Forward DataService-side errors verbatim (table_not_found,
        # empty_table, row_count_exceeds_limit, …) so callers can branch.
        return export
    table_name    = export.get("table_name")
    row_count     = int(export.get("row_count") or 0)
    presigned_url = export.get("presigned_url")
    if not table_name or not presigned_url:
        return {"error": f"export_full returned malformed response: {export}"}

    # Stream CSV → temp file → chunked Parquet.
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    tmp_csv  = SESSION_DIR / f"{table_name}.csv.tmp"
    parquet  = SESSION_DIR / f"{table_name}.parquet"

    try:
        async with httpx.AsyncClient(http2=True, timeout=300.0) as http:
            async with http.stream("GET", presigned_url) as resp:
                resp.raise_for_status()
                # The export endpoint serves a ZIP archive that contains a
                # single CSV; if it's a plain CSV (small response) we fall
                # back to raw-write. Detect by content-type.
                ctype = resp.headers.get("content-type", "")
                with tmp_csv.open("wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        f.write(chunk)

        # If the file is a ZIP, stream the contained CSV directly into the
        # parquet writer — no second extracted file on disk.
        if _looks_like_zip(tmp_csv) or "zip" in ctype:
            import zipfile
            with zipfile.ZipFile(tmp_csv) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not names:
                    return {"error": "export zip contains no csv"}
                with zf.open(names[0]) as csv_stream:
                    total_rows = _stream_csv_to_parquet(csv_stream, parquet, pa, pd, pq)
            tmp_csv.unlink(missing_ok=True)
            tmp_csv = None  # already removed; skip the finally cleanup
        else:
            # Plain CSV — read from the already-downloaded temp file.
            with tmp_csv.open("rb") as csv_stream:
                total_rows = _stream_csv_to_parquet(csv_stream, parquet, pa, pd, pq)

        meta = get_session().set(
            symbol=symbol,
            timeframe=timeframe,
            table_name=table_name,
            parquet_path=parquet,
            row_count=total_rows,
        )
        return {"loaded": True, **meta}
    finally:
        # Remove the temp file when it still exists (error path or plain CSV).
        if tmp_csv is not None:
            try:
                tmp_csv.unlink(missing_ok=True)
            except OSError:
                pass


async def _handle_dbscan(envelope) -> dict:
    """Run DBSCAN on the currently loaded session.

    Reads only the requested columns from the parquet file, systematically
    sub-samples to ``max_sample_rows`` rows, scales features, fits DBSCAN,
    and reports anomaly timestamps. The DataFrame is freed immediately
    after the fit.
    """
    import pandas as pd
    from sklearn.cluster import DBSCAN
    from sklearn.preprocessing import StandardScaler

    payload = envelope.payload or {}
    eps             = float(payload.get("eps", 0.5))
    min_samples     = int(payload.get("min_samples", 5))
    max_sample_rows = int(payload.get("max_sample_rows", 50_000))
    columns         = payload.get("columns") or [
        "close_price", "volume", "turnover", "open_interest"
    ]

    parquet = get_session().get_parquet_path()
    meta    = get_session().get_metadata()
    if parquet is None or meta is None:
        return {"error": "no_session_loaded"}

    # Read only the columns we need + timestamp_utc for the anomaly index.
    # Use bounded read: for large sessions only a proportional subset of row
    # groups is read from disk, limiting peak I/O and memory to ~max_sample_rows.
    needed = ["timestamp_utc", *columns]
    df = None
    sample = None
    try:
        total_rows = meta.get("row_count") or 0
        df = read_parquet_bounded(parquet, needed, max_sample_rows, total_rows)
        # Drop any column that was missing from this table.
        present = [c for c in columns if c in df.columns]
        if not present:
            return {"error": "none of the requested columns are present"}
        df = df.dropna(subset=present)
        total = len(df)
        if total == 0:
            return {"error": "empty_after_dropna"}

        # Systematic sampling — cheaper than .sample() for huge frames and
        # preserves temporal structure. We only ever read from `sample`
        # (slice columns / .astype()), so a view here is correct: no
        # SettingWithCopyWarning can fire. Avoiding the copy halves peak RAM
        # for the largest sessions.
        step = max(1, total // max_sample_rows)
        sample = df.iloc[::step] if step > 1 else df

        X = StandardScaler().fit_transform(sample[present].astype("float32").to_numpy())
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(X)

        anomaly_mask = labels == -1
        anomaly_ts = sample.loc[anomaly_mask, "timestamp_utc"].astype("int64").tolist()
        n_clusters = int(len({l for l in labels if l != -1}))

        return {
            "summary": {
                "total_rows":  total,
                "sample_size": int(len(sample)),
                "n_clusters":  n_clusters,
                "n_anomalies": int(anomaly_mask.sum()),
                "eps":         eps,
                "min_samples": min_samples,
                "columns":     present,
            },
            "anomaly_timestamps_ms": anomaly_ts,
        }
    finally:
        # Eager cleanup — do not let big frames live until the next GC tick.
        del df
        del sample
        gc.collect()


def _looks_like_zip(path) -> bool:
    """Cheap magic-number sniff for a ZIP file."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _stream_csv_to_parquet(csv_stream, parquet_path, pa, pd, pq) -> int:
    """Consume *csv_stream* (any file-like object) and write a Snappy-compressed
    Parquet file at *parquet_path*, returning the total number of rows written.

    Implementation notes:
      • Parsing is done by ``pyarrow.csv.open_csv`` which streams the file
        in C-level batches (no per-chunk Python loop, no intermediate
        ``DataFrame``).
      • After each batch we cast every ``float64`` column (except
        ``timestamp_utc``) to ``float32`` at the Arrow level — one C-level
        ``cast`` per batch instead of a per-column Python ``astype`` round
        trip through pandas. This halves on-disk size without touching
        precision-sensitive timestamp columns.
      • ``pd`` is no longer needed here but kept in the signature so the
        old call sites keep working without parameter changes.

    The caller is responsible for deleting any temporary source file.
    """
    import pyarrow.csv as pacsv  # local import: pyarrow itself is already loaded

    # 8 MB read blocks — tuned to amortise OS-read overhead while keeping
    # peak memory bounded; one block roughly matches one Parquet row group.
    read_opts = pacsv.ReadOptions(block_size=8 * 1024 * 1024)

    writer = None
    total_rows = 0
    try:
        with pacsv.open_csv(csv_stream, read_options=read_opts) as reader:
            for batch in reader:
                # Down-cast every 64-bit float column (except the timestamp)
                # to 32-bit. We rebuild the schema/columns lazily so we don't
                # touch columns that are already narrow.
                fields = batch.schema
                changed = False
                new_arrays = []
                new_fields = []
                for field in fields:
                    arr = batch.column(field.name)
                    if (field.name != "timestamp_utc"
                            and pa.types.is_floating(field.type)
                            and field.type.bit_width == 64):
                        arr = arr.cast(pa.float32())
                        new_fields.append(pa.field(field.name, pa.float32()))
                        changed = True
                    else:
                        new_fields.append(field)
                    new_arrays.append(arr)
                if changed:
                    batch = pa.RecordBatch.from_arrays(
                        new_arrays, schema=pa.schema(new_fields))

                if writer is None:
                    writer = pq.ParquetWriter(
                        parquet_path, batch.schema, compression="snappy")
                writer.write_batch(batch)
                total_rows += batch.num_rows
    finally:
        if writer is not None:
            writer.close()
    return total_rows


# ── Quality / repair handlers ────────────────────────────────────────────────
#
# These wrappers pull the dataset.quality and dataset.repair modules into
# the Kafka surface area. They run inside the background asyncio loop, so
# we use the live KafkaClient directly (no run_coroutine_threadsafe).

async def _handle_quality_check(envelope) -> dict:
    """Audit per-group fill ratios for a market table."""
    from .dataset.quality import audit_dataset
    payload = envelope.payload or {}
    table = payload.get("table")
    if not table:
        return {"error": "missing field: table"}
    client = _client_obj()
    return await audit_dataset(
        table_name=table,
        request=lambda topic, p: client.request(topic, p, timeout=45.0),
    )


async def _handle_load_ohlcv(envelope) -> dict:
    """Fetch OHLCV from Bybit and upsert into the existing table."""
    from .dataset.repair import load_ohlcv
    payload = envelope.payload or {}
    symbol    = payload.get("symbol")
    timeframe = payload.get("timeframe")
    start_ms  = payload.get("start_ms")
    end_ms    = payload.get("end_ms")
    if not symbol or not timeframe or start_ms is None or end_ms is None:
        return {"error": "missing fields: symbol, timeframe, start_ms, end_ms"}
    client = _client_obj()
    return await load_ohlcv(
        symbol=str(symbol),
        timeframe=str(timeframe),
        start_ms=int(start_ms),
        end_ms=int(end_ms),
        correlation_id=envelope.correlation_id or "",
        request=lambda topic, p, **kw: client.request(topic, p, **kw),
        publish=lambda topic, env: client.send(topic, env),
    )


async def _handle_recompute_features(envelope) -> dict:
    """Recompute OHLCV/RSI feature columns by delegating to the data service."""
    from .dataset.repair import recompute_features
    payload = envelope.payload or {}
    symbol    = payload.get("symbol")
    timeframe = payload.get("timeframe")
    if not symbol or not timeframe:
        return {"error": "missing fields: symbol, timeframe"}
    client = _client_obj()
    return await recompute_features(
        symbol=str(symbol),
        timeframe=str(timeframe),
        correlation_id=envelope.correlation_id or "",
        request=lambda topic, p, **kw: client.request(topic, p, **kw),
        publish=lambda topic, env: client.send(topic, env),
    )


def _client_obj() -> "KafkaClient":
    """Return the current ``KafkaClient`` instance (must be initialised)."""
    if _client is None:  # pragma: no cover — defensive
        raise RuntimeError("KafkaClient is not initialised; call start() first")
    return _client


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
                client.start(subscribe=[
                    CMD_ANALYTICS_HEALTH,
                    CMD_ANALYTICS_MODEL_LIST,
                    CMD_ANALITIC_DATASET_LOAD,
                    CMD_ANALITIC_DATASET_UNLOAD,
                    CMD_ANALITIC_DATASET_STATUS,
                    CMD_ANALITIC_ANOMALY_DBSCAN,
                    CMD_ANALITIC_ANOMALY_ISOLATION_FOREST,
                    CMD_ANALITIC_DATASET_DISTRIBUTION,
                    CMD_ANALITIC_DATASET_QUALITY_CHECK,
                    CMD_ANALITIC_DATASET_LOAD_OHLCV,
                    CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
                ]), _loop
            )
            future.result(timeout=30)
            # Local imports — keep cold-start light by deferring sklearn/scipy
            # heavy modules until they're actually used.
            from backend.anomaly.isolation_forest import handle_isolation_forest
            from backend.anomaly.distribution    import handle_distribution

            client.register_handler(CMD_ANALYTICS_HEALTH, _handle_health)
            client.register_handler(CMD_ANALYTICS_MODEL_LIST, _handle_model_list)
            client.register_handler(CMD_ANALITIC_DATASET_LOAD, _handle_dataset_load)
            client.register_handler(CMD_ANALITIC_DATASET_UNLOAD, _handle_dataset_unload)
            client.register_handler(CMD_ANALITIC_DATASET_STATUS, _handle_dataset_status)
            client.register_handler(CMD_ANALITIC_ANOMALY_DBSCAN, _handle_dbscan)
            client.register_handler(CMD_ANALITIC_ANOMALY_ISOLATION_FOREST, handle_isolation_forest)
            client.register_handler(CMD_ANALITIC_DATASET_DISTRIBUTION, handle_distribution)
            client.register_handler(CMD_ANALITIC_DATASET_QUALITY_CHECK, _handle_quality_check)
            client.register_handler(CMD_ANALITIC_DATASET_LOAD_OHLCV, _handle_load_ohlcv)
            client.register_handler(CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES, _handle_recompute_features)
            _client = client
            # Clear leftover parquet files from a previous (possibly crashed) run.
            try:
                reset_session_dir()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("[data_client] reset_session_dir failed: %s", exc)
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
