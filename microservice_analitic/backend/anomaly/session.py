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
            if old is not None and old.parquet_path != parquet_path:
                _silent_unlink(old.parquet_path)
            return self._public_meta(self._meta)

    def clear(self) -> bool:
        """Drop the session and remove the parquet file. Returns ``True``
        if a session was active."""
        with self._lock:
            old = self._meta
            self._meta = None
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
    n_rgs = pf.num_row_groups
    total = total_known if total_known is not None else pf.metadata.num_rows

    if max_rows is None or n_rgs == 0 or total <= max_rows:
        return pd.read_parquet(parquet_path, columns=columns)

    # Select row groups at uniform intervals so we cover the full temporal
    # range with approximately max_rows rows. Reading is I/O-bounded to the
    # selected row groups (pyarrow skips unselected ones at the block level).
    rows_per_rg = max(1, total // n_rgs)
    target_rgs = max(1, (max_rows + rows_per_rg - 1) // rows_per_rg)
    rg_step = max(1, n_rgs // target_rgs)
    selected = list(range(0, n_rgs, rg_step))

    tbl = pf.read_row_groups(selected, columns=columns)
    return tbl.to_pandas()


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
    n_rgs = pf.num_row_groups
    total = total_known if total_known is not None else pf.metadata.num_rows

    if max_rows is None or n_rgs == 0 or total <= max_rows:
        return pd.read_parquet(parquet_path, columns=columns)

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

    tbl = pf.read_row_groups(selected, columns=columns)
    return tbl.to_pandas()
