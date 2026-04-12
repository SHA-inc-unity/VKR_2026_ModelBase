from __future__ import annotations

from typing import Any

DEFAULT_COMPARISON_FIELDS = [
    "delta_vs_baseline",
    "raw_model_delta_vs_baseline",
    "mean_delta_vs_baseline",
    "std_delta_vs_baseline",
    "win_rate_vs_baseline",
    "sign_acc_pct",
    "direction_acc_pct",
    "overfit_status",
    "robustness_status",
    "selection_eligibility",
    "recommendation_bucket",
    "guarded_candidate_type",
]


def build_comparison_rows(
    snapshots: dict[str, dict[str, Any]],
    *,
    model_keys: list[str] | None = None,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    fields = list(fields or DEFAULT_COMPARISON_FIELDS)
    keys = list(model_keys or snapshots.keys())
    rows: list[dict[str, Any]] = []
    for key in keys:
        snapshot = dict(snapshots.get(key, {}) or {})
        if not snapshot:
            continue
        registry = dict(snapshot.get("registry", {}) or {})
        row = {
            "model_key": key,
            "model_name": snapshot.get("model_name", registry.get("model_name", key)),
        }
        for field in fields:
            row[field] = registry.get(field)
        rows.append(row)
    return rows