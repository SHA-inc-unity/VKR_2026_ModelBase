"""Dataset quality audit.

Inspects an existing market-data table and reports the fill ratio of three
semantic column groups:

* ``ohlcv_raw``       — raw OHLCV columns (open/high/low/volume/turnover).
* ``ohlcv_derived``   — features computed from OHLCV (ATR, candle structure,
                        volume rolling features).
* ``rsi_derived``     — features computed from RSI (currently rsi_slope).

For each group we issue a single ``cmd.data.dataset.column_stats`` request
and aggregate the per-column non-null counts into a group fill percentage.

The output is consumed by the front-end "Качество датасета" block and by
the orchestration handlers in :mod:`backend.dataset.repair`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_LOG = logging.getLogger(__name__)

# ── Group definitions ────────────────────────────────────────────────────────
#
# Column lists must stay in sync with
#   microservice_data/src/DataService.API/Dataset/DatasetConstants.cs
# (RawTableSchema + FeatureTableSchema). Update both when adding features.
#
# The repair_action values are matched by the front-end to decide which
# "Исправить" button to show.

OHLCV_RAW_COLUMNS: tuple[str, ...] = (
    "open_price",
    "high_price",
    "low_price",
    "volume",
    "turnover",
)

OHLCV_DERIVED_COLUMNS: tuple[str, ...] = (
    # ATR (rolling true-range)
    "atr_6",
    "atr_24",
    # Candle structure
    "candle_body",
    "upper_wick",
    "lower_wick",
    # Volume rolling features
    "volume_roll6_mean",
    "volume_roll24_mean",
    "volume_to_roll6_mean",
    "volume_to_roll24_mean",
    "volume_return_1",
)

RSI_DERIVED_COLUMNS: tuple[str, ...] = (
    "rsi_slope",
)


@dataclass(frozen=True)
class QualityGroup:
    """Static description of one audited column group."""
    id: str
    label: str
    columns: tuple[str, ...]
    repair_action: str  # "load_ohlcv" | "recompute_features"


QUALITY_GROUPS: tuple[QualityGroup, ...] = (
    QualityGroup(
        id="ohlcv_raw",
        label="OHLCV-сырые",
        columns=OHLCV_RAW_COLUMNS,
        repair_action="load_ohlcv",
    ),
    QualityGroup(
        id="ohlcv_derived",
        label="Производные от OHLCV",
        columns=OHLCV_DERIVED_COLUMNS,
        repair_action="recompute_features",
    ),
    QualityGroup(
        id="rsi_derived",
        label="Производные от RSI",
        columns=RSI_DERIVED_COLUMNS,
        repair_action="recompute_features",
    ),
)


# ── Status thresholds ────────────────────────────────────────────────────────
#
# fill_pct >= 99 → "full"     (green)
# fill_pct >= 1  → "partial"  (yellow)
# fill_pct <  1  → "missing"  (red)

_FULL_THRESHOLD: float = 99.0
_PARTIAL_THRESHOLD: float = 1.0


def _classify(fill_pct: float) -> str:
    if fill_pct >= _FULL_THRESHOLD:
        return "full"
    if fill_pct >= _PARTIAL_THRESHOLD:
        return "partial"
    return "missing"


# ── Public API ───────────────────────────────────────────────────────────────

# A `RequestFn` is anything that takes (topic, payload) and returns the
# parsed reply payload. We keep this protocol-light so callers can pass
# either a lambda over the shared async client or a thin sync wrapper.
RequestFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _empty_report(table_name: str) -> dict[str, Any]:
    """Return a valid quality report for a table that does not exist yet.

    All groups are reported as ``"missing"`` with ``fill_pct = 0``.  This is
    the correct semantic for *no data loaded* rather than *application error*.
    """
    return {
        "table":      table_name,
        "total_rows": 0,
        "groups": [
            {
                "id":            g.id,
                "label":         g.label,
                "columns":       list(g.columns),
                "fill_pct":      0.0,
                "status":        "missing",
                "repair_action": g.repair_action,
            }
            for g in QUALITY_GROUPS
        ],
    }


async def audit_dataset(
    table_name: str,
    request: RequestFn,
) -> dict[str, Any]:
    """Audit fill ratios for the configured quality groups.

    :param table_name: name of the market-data table.
    :param request: async ``(topic, payload) -> reply_payload`` function;
        used to call ``cmd.data.dataset.column_stats``.

    Returns a dict with shape::

        {
          "table":      "btcusdt_5m",
          "total_rows": 12345,
          "groups": [
            { "id": ..., "label": ..., "columns": [...],
              "fill_pct": 92.4, "status": "partial",
              "repair_action": "load_ohlcv" },
            ...
          ]
        }

    On a missing table or any backend error, returns ``{"error": str}``.
    """
    from modelline_shared.messaging.topics import CMD_DATA_DATASET_COLUMN_STATS

    if not table_name:
        return {"error": "missing field: table"}

    try:
        # Request only the 16 columns that the quality audit actually inspects,
        # and skip the expensive MIN/MAX/AVG/STDDEV aggregates — fill ratios
        # only need non-null counts. This reduces ~150 SQL aggregates to ~17.
        all_cols = [col for g in QUALITY_GROUPS for col in g.columns]
        reply = await request(
            CMD_DATA_DATASET_COLUMN_STATS,
            {
                "table":      table_name,
                "columns":    all_cols,
                "count_only": True,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("audit_dataset | column_stats failed")
        return {"error": f"column_stats failed: {exc}"}

    if "error" in reply:
        error_msg = str(reply["error"])
        if "table not found" in error_msg.lower():
            # Table does not exist yet — semantically equivalent to "no data
            # loaded".  Return a valid all-missing report so the caller can
            # present the repair buttons rather than treating this as a crash.
            _LOG.debug("audit_dataset | table %r not found — returning empty report", table_name)
            return _empty_report(table_name)
        return {"error": error_msg}

    total_rows = int(reply.get("total_rows", 0) or 0)
    cols = reply.get("columns") or []

    # Index by column name for O(1) lookup per group.
    col_by_name: dict[str, dict[str, Any]] = {
        str(c.get("name")): c for c in cols if c.get("name")
    }

    groups_out: list[dict[str, Any]] = []
    for group in QUALITY_GROUPS:
        # Sum non-nulls across the group, divide by (total_rows × n_cols).
        if total_rows <= 0 or not group.columns:
            fill_pct = 0.0
        else:
            non_null_total = 0
            denom = total_rows * len(group.columns)
            for col_name in group.columns:
                col = col_by_name.get(col_name)
                if col is None:
                    # Column missing from schema → counts as 0% for this column.
                    continue
                non_null_total += int(col.get("non_null", 0) or 0)
            fill_pct = (non_null_total * 100.0 / denom) if denom > 0 else 0.0

        groups_out.append({
            "id":            group.id,
            "label":         group.label,
            "columns":       list(group.columns),
            "fill_pct":      round(fill_pct, 2),
            "status":        _classify(fill_pct),
            "repair_action": group.repair_action,
        })

    return {
        "table":      table_name,
        "total_rows": total_rows,
        "groups":     groups_out,
    }
