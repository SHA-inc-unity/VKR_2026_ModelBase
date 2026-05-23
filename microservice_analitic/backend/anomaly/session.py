"""Process-wide dataset session backed by an on-disk Parquet file.

The :class:`DatasetSession` singleton stores a *reference* to the currently
loaded dataset (symbol, timeframe, parquet path, row count) — never the
DataFrame itself. Heavy operations such as DBSCAN read the Parquet file
lazily into a small sample, run, and immediately drop the sample.

Why on-disk Parquet?
====================
Holding a 5M-row DataFrame in Python's heap easily costs 1–3 GB of RAM and
fragments memory across the process lifetime. Parquet on a tmpfs path keeps
the working set on disk, lets us cast ``float64 → float32`` per chunk during
import, and gives us O(1) memory cost when the session is "loaded" but idle.

Concurrency
===========
A single ``threading.Lock`` guards mutations (set / clear). All reads happen
through getter methods that copy out small primitive values, so callers don't
need to hold the lock.
"""
from __future__ import annotations

from collections import OrderedDict
import gc
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger(__name__)

# Hard limit. We refuse to load more than this many rows to keep DBSCAN
# tractable and avoid pathological /tmp usage.
MAX_SESSION_ROWS: int = 5_000_000

# Parquet files live under this dir, one file per loaded session. The dir
# is wiped on service start (see ``data_client.start``) to clear leftovers
# from a prior crash.
SESSION_DIR: Path = Path(os.environ.get("MODELLINE_SESSION_DIR", "/tmp/modelline_sessions"))
READ_CACHE_MAX_ENTRIES: int = int(os.environ.get("MODELLINE_SESSION_READ_CACHE_ENTRIES", "6"))

_READ_CACHE_LOCK = threading.Lock()
_READ_CACHE: OrderedDict[tuple, object] = OrderedDict()


@dataclass
class _Meta:
    symbol: str
    timeframe: str
    table_name: str
    row_count: int
    memory_mb_on_disk: float
    parquet_path: Path
    loaded_at: float


class DatasetSession:
    """Singleton holder for the currently loaded dataset session."""

    _instance: Optional["DatasetSession"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        # ``_lock`` guards ``_meta`` mutations; reads copy primitives out.
        self._lock = threading.Lock()
        self._meta: _Meta | None = None
        SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # ── lifecycle ────────────────────────────────────────────────────────
    def set(
        self,
        *,
        symbol: str,
        timeframe: str,
        table_name: str,
        parquet_path: Path,
        row_count: int,
    ) -> dict:
        """Atomically replace the active session and drop any prior parquet.

        Returns the new metadata as a dict (without the parquet path, which
        is private to the session).
        """
        with self._lock:
            old = self._meta
            mb = parquet_path.stat().st_size / (1024 * 1024) if parquet_path.exists() else 0.0
            self._meta = _Meta(
                symbol=symbol,
                timeframe=timeframe,
                table_name=table_name,
                row_count=row_count,
                memory_mb_on_disk=round(mb, 2),
                parquet_path=parquet_path,
                loaded_at=time.time(),
            )
            _clear_read_cache()
            if old is not None and old.parquet_path != parquet_path:
                _silent_unlink(old.parquet_path)
            return self._public_meta(self._meta)

    def clear(self) -> bool:
        """Drop the session and remove the parquet file. Returns ``True``
        if a session was active."""
        with self._lock:
            old = self._meta
            self._meta = None
            _clear_read_cache()
        if old is None:
            return False
        _silent_unlink(old.parquet_path)
        gc.collect()
        return True

    # ── reads ────────────────────────────────────────────────────────────
    def get_metadata(self) -> dict | None:
        with self._lock:
            return self._public_meta(self._meta) if self._meta else None

    def get_parquet_path(self) -> Path | None:
        with self._lock:
            return self._meta.parquet_path if self._meta else None

    def is_loaded_for(self, symbol: str, timeframe: str) -> bool:
        with self._lock:
            return (
                self._meta is not None
                and self._meta.symbol == symbol
                and self._meta.timeframe == timeframe
            )

    @staticmethod
    def _public_meta(meta: _Meta | None) -> dict | None:
        if meta is None:
            return None
        return {
            "symbol":            meta.symbol,
            "timeframe":         meta.timeframe,
            "table_name":        meta.table_name,
            "row_count":         meta.row_count,
            "memory_mb_on_disk": meta.memory_mb_on_disk,
            "loaded_at":         meta.loaded_at,
        }


def get_session() -> DatasetSession:
    """Return the process-wide session singleton (lazy init)."""
    if DatasetSession._instance is None:
        with DatasetSession._instance_lock:
            if DatasetSession._instance is None:
                DatasetSession._instance = DatasetSession()
    return DatasetSession._instance


def reset_session_dir() -> None:
    """Wipe the on-disk session directory. Call at service startup to clean
    up parquet files left from a previous (possibly crashed) run."""
    try:
        _clear_read_cache()
        if SESSION_DIR.exists():
            shutil.rmtree(SESSION_DIR)
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOG.warning("Could not reset session dir %s: %s", SESSION_DIR, exc)


def _silent_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        _LOG.warning("Could not delete parquet %s: %s", path, exc)


def _clear_read_cache() -> None:
    with _READ_CACHE_LOCK:
        _READ_CACHE.clear()


def _cache_key(
    parquet_path: Path,
    mode: str,
    columns: list[str],
    max_rows: int | None,
    total_rows: int,
) -> tuple:
    try:
        mtime_ns = parquet_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return (str(parquet_path), mtime_ns, mode, tuple(columns), max_rows, total_rows)


def _get_cached_frame(key: tuple) -> "pd.DataFrame | None":
    with _READ_CACHE_LOCK:
        cached = _READ_CACHE.get(key)
        if cached is None:
            return None
        _READ_CACHE.move_to_end(key)
    return cached.copy(deep=False)


def _store_cached_frame(key: tuple, frame: "pd.DataFrame") -> "pd.DataFrame":
    with _READ_CACHE_LOCK:
        _READ_CACHE[key] = frame
        _READ_CACHE.move_to_end(key)
        while len(_READ_CACHE) > READ_CACHE_MAX_ENTRIES:
            _READ_CACHE.popitem(last=False)
    return frame.copy(deep=False)


def _resolve_projection_columns(
    available_columns: set[str],
    requested_columns: list[str],
) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()

    for column in requested_columns:
        physical = column
        if column not in available_columns:
            if column == "timestamp_ms" and "timestamp_utc" in available_columns:
                physical = "timestamp_utc"
            elif column == "timestamp_utc" and "timestamp_ms" in available_columns:
                physical = "timestamp_ms"
            else:
                continue
        if physical not in seen:
            resolved.append(physical)
            seen.add(physical)

    return resolved


def _finalize_projection(frame: "pd.DataFrame", requested_columns: list[str]) -> "pd.DataFrame":
    if "timestamp_ms" not in requested_columns and "timestamp_utc" not in requested_columns:
        return frame

    import pandas as pd

    if "timestamp_ms" in frame.columns:
        series = pd.to_numeric(frame["timestamp_ms"], errors="raise").astype("int64", copy=False)
        if str(series.dtype) == "int64" and series is frame["timestamp_ms"]:
            return frame
        normalized = frame.copy(deep=False)
        normalized["timestamp_ms"] = series.to_numpy(copy=False)
        return normalized

    if "timestamp_utc" not in frame.columns:
        return frame

    ts = frame["timestamp_utc"]
    if getattr(ts.dtype, "kind", "") == "M" or getattr(ts.dtype, "tz", None) is not None:
        timestamp_ms = (pd.to_datetime(ts, utc=True).astype("int64") // 1_000_000).astype("int64", copy=False)
    else:
        timestamp_ms = pd.to_numeric(ts, errors="raise").astype("int64", copy=False)

    normalized = frame.copy(deep=False)
    normalized["timestamp_ms"] = timestamp_ms.to_numpy(copy=False)
    return normalized


def read_parquet_bounded(
    parquet_path: "Path",
    columns: list[str],
    max_rows: int | None,
    total_known: int | None = None,
) -> "pd.DataFrame":
    """Read a parquet file with bounded disk I/O.

    When ``max_rows`` is None or the session fits within the budget, the
    entire file is read (column-projected only). When the session is large,
    row groups are selected at systematic intervals so the physical read is
    proportional to ``max_rows`` rather than the full row count — the caller
    still applies a fine-grained ``iloc[::step]`` afterwards, but the peak
    memory is already bounded to approximately ``max_rows`` rows.

    Args:
        parquet_path: On-disk parquet file.
        columns: Column projection (passed straight to pyarrow).
        max_rows: Sample budget. ``None`` means no limit.
        total_known: Row count from session metadata (avoids reading the
            parquet footer just to get ``num_rows``).
    """
    import pandas as pd
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(parquet_path)
    resolved_columns = _resolve_projection_columns(set(pf.schema_arrow.names), columns)
    n_rgs = pf.num_row_groups
    total = total_known if total_known is not None else pf.metadata.num_rows
    key = _cache_key(parquet_path, "bounded", resolved_columns, max_rows, total)
    cached = _get_cached_frame(key)
    if cached is not None:
        return cached

    if max_rows is None or n_rgs == 0 or total <= max_rows:
        df = pd.read_parquet(parquet_path, columns=resolved_columns)
        return _store_cached_frame(key, _finalize_projection(df, columns))

    # Select row groups at uniform intervals so we cover the full temporal
    # range with approximately max_rows rows. Reading is I/O-bounded to the
    # selected row groups (pyarrow skips unselected ones at the block level).
    rows_per_rg = max(1, total // n_rgs)
    target_rgs = max(1, (max_rows + rows_per_rg - 1) // rows_per_rg)
    rg_step = max(1, n_rgs // target_rgs)
    selected = list(range(0, n_rgs, rg_step))

    tbl = pf.read_row_groups(selected, columns=resolved_columns)
    df = tbl.to_pandas()
    return _store_cached_frame(key, _finalize_projection(df, columns))


def read_parquet_contiguous(
    parquet_path: "Path",
    columns: list[str],
    max_rows: int | None,
    total_known: int | None = None,
) -> "pd.DataFrame":
    """Read a contiguous tail-slice of a parquet file with bounded I/O.

    Unlike :func:`read_parquet_bounded`, the selected row groups are
    *consecutive* and aligned to the end of the file. This guarantees that
    consecutive rows in the returned DataFrame are also consecutive in the
    original time series — required for any statistic that depends on
    adjacency (log-returns, autocorrelation, JB on returns, etc.).

    The "tail" choice gives diagnostics that reflect the most recent
    market state. When ``max_rows`` is None or the session fits within the
    budget the whole file is read.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(parquet_path)
    resolved_columns = _resolve_projection_columns(set(pf.schema_arrow.names), columns)
    n_rgs = pf.num_row_groups
    total = total_known if total_known is not None else pf.metadata.num_rows
    key = _cache_key(parquet_path, "contiguous", resolved_columns, max_rows, total)
    cached = _get_cached_frame(key)
    if cached is not None:
        return cached

    if max_rows is None or n_rgs == 0 or total <= max_rows:
        df = pd.read_parquet(parquet_path, columns=resolved_columns)
        return _store_cached_frame(key, _finalize_projection(df, columns))

    # Walk row groups from the last one backwards, accumulating until we hit
    # the budget. Resulting indices are then sorted ascending so the returned
    # DataFrame preserves chronological order.
    selected: list[int] = []
    rows_acc = 0
    for rg in range(n_rgs - 1, -1, -1):
        rg_rows = pf.metadata.row_group(rg).num_rows
        selected.append(rg)
        rows_acc += rg_rows
        if rows_acc >= max_rows:
            break
    selected.sort()

    tbl = pf.read_row_groups(selected, columns=resolved_columns)
    df = tbl.to_pandas()
    return _store_cached_frame(key, _finalize_projection(df, columns))
