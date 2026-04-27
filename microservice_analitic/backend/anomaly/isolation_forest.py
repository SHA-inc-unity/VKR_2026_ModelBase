"""Isolation-Forest anomaly detector for the loaded dataset session.

Lives next to the DBSCAN handler in :mod:`backend.data_client` and shares the
same execution model:

* reads only the requested columns from the on-disk Parquet sample,
* sub-samples to ``max_sample_rows`` to keep memory bounded,
* fits :class:`sklearn.ensemble.IsolationForest`,
* returns the timestamps that were classified as outliers.

Why Isolation Forest as a *second* method?
==========================================
DBSCAN is density-based and sensitive to ``eps``; Isolation Forest is tree-
based, scales linearly with row count (~O(n log n)), and works well when
anomalies are easy to isolate but the cluster shape is irregular. The two
detectors complement each other — the Anomaly page exposes both side by
side so the user can compare results.
"""
from __future__ import annotations

import gc
import logging
from typing import Any

_LOG = logging.getLogger(__name__)

# Sensible defaults — match the DBSCAN handler so users don't get jarred by
# different ranges between the two cards.
DEFAULT_COLUMNS  = ["close_price", "volume", "turnover", "open_interest"]
DEFAULT_CONTAM   = 0.01
DEFAULT_TREES    = 100
DEFAULT_MAX_ROWS = 50_000


def _coerce_float(value: Any, default: float, lo: float, hi: float) -> float:
    """Best-effort numeric coercion with bounds. Returns ``default`` on bad input."""
    try:
        v = float(value) if value is not None else default
    except (TypeError, ValueError):
        v = default
    if v < lo: v = lo
    if v > hi: v = hi
    return v


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value) if value is not None else default
    except (TypeError, ValueError):
        v = default
    if v < lo: v = lo
    if v > hi: v = hi
    return v


async def handle_isolation_forest(envelope) -> dict:
    """Run Isolation Forest on the active dataset session.

    Payload fields (all optional):

    * ``contamination``    — expected outlier share, ``[0.0001, 0.5]``.
    * ``n_estimators``     — number of trees, ``[20, 500]``.
    * ``max_sample_rows``  — sub-sample cap, ``[1000, 1_000_000]``.
    * ``columns``          — feature columns; defaults to OHLCV-derived.

    Reply:

    .. code-block:: json

        {
          "summary": {
            "total_rows":  int,
            "sample_size": int,
            "n_anomalies": int,
            "contamination": float,
            "n_estimators": int,
            "columns":     ["..."]
          },
          "anomaly_timestamps_ms": [int, ...]
        }
    """
    # Heavy imports — only paid when this handler is hit.
    import numpy as np
    import pandas as pd
    from sklearn.ensemble import IsolationForest
    from backend.anomaly.session import get_session, read_parquet_bounded

    payload         = envelope.payload or {}
    contamination   = _coerce_float(payload.get("contamination"),  DEFAULT_CONTAM,   1e-4, 0.5)
    n_estimators    = _coerce_int  (payload.get("n_estimators"),   DEFAULT_TREES,    20, 500)
    max_sample_rows = _coerce_int  (payload.get("max_sample_rows"), DEFAULT_MAX_ROWS, 1_000, 1_000_000)
    columns         = payload.get("columns") or DEFAULT_COLUMNS

    parquet = get_session().get_parquet_path()
    meta    = get_session().get_metadata()
    if parquet is None or meta is None:
        return {"error": "no_session_loaded"}

    # Read only the columns we need + timestamp_utc for the anomaly index.
    # Bounded read: for large sessions only proportional row groups are
    # read from disk, so peak I/O and memory scale with max_sample_rows.
    needed = ["timestamp_utc", *columns]
    df = None
    sample = None
    try:
        total_rows = meta.get("row_count") or 0
        df = read_parquet_bounded(parquet, needed, max_sample_rows, total_rows)
        present = [c for c in columns if c in df.columns]
        if not present:
            return {"error": "none of the requested columns are present"}
        df = df.dropna(subset=present)
        total = len(df)
        if total == 0:
            return {"error": "empty_after_dropna"}

        # Systematic sampling preserves temporal order, which Isolation Forest
        # is technically agnostic to but we want consistency with DBSCAN.
        # `sample` is read-only below (.astype creates a new ndarray) — a
        # view is safe and avoids doubling the memory footprint of the slice.
        step = max(1, total // max_sample_rows)
        sample = df.iloc[::step] if step > 1 else df

        X = sample[present].astype("float32").to_numpy()
        # ``random_state`` — stable across reruns so users can reason about diffs.
        # ``n_jobs=-1`` — use all CPU cores; tree fitting is embarrassingly parallel.
        clf = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=42,
            n_jobs=-1,
        )
        labels = clf.fit_predict(X)

        anomaly_mask = labels == -1
        anomaly_ts = sample.loc[anomaly_mask, "timestamp_utc"].astype("int64").tolist()

        return {
            "summary": {
                "total_rows":    total,
                "sample_size":   int(len(sample)),
                "n_anomalies":   int(anomaly_mask.sum()),
                "contamination": contamination,
                "n_estimators":  n_estimators,
                "columns":       present,
            },
            "anomaly_timestamps_ms": anomaly_ts,
        }
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("isolation_forest | failed")
        return {"error": str(exc)}
    finally:
        # Eager cleanup — do not let big frames live until the next GC tick.
        del df
        del sample
        gc.collect()
