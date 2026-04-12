from __future__ import annotations

from typing import Any

from catboost_floader.diagnostics.model_snapshot import build_model_registry_rows

from catboost_floader.frontend_api.dto import DashboardOverviewDTO, ModelRegistryEntry


def _safe_float(value: Any) -> float | None:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if value_f != value_f:
        return None
    return value_f


def _is_robust_status(status: Any) -> bool:
    return str(status or "").lower().startswith("robust")


def get_model_registry_entries() -> list[ModelRegistryEntry]:
    entries: list[ModelRegistryEntry] = []
    for row in build_model_registry_rows():
        entries.append(
            ModelRegistryEntry(
                model_key=str(row.get("model_key")),
                model_name=str(row.get("model_name")),
                is_main=bool(row.get("is_main", False)),
                robustness_status=row.get("robustness_status"),
                selection_eligibility=row.get("selection_eligibility"),
                delta_vs_baseline=_safe_float(row.get("delta_vs_baseline")),
                mean_delta_vs_baseline=_safe_float(row.get("mean_delta_vs_baseline")),
                std_delta_vs_baseline=_safe_float(row.get("std_delta_vs_baseline")),
                win_rate_vs_baseline=_safe_float(row.get("win_rate_vs_baseline")),
                sign_acc_pct=_safe_float(row.get("sign_acc_pct")),
                direction_acc_pct=_safe_float(row.get("direction_acc_pct")),
                overfit_status=row.get("overfit_status"),
                overfit_reason=row.get("overfit_reason"),
                raw_model_delta_vs_baseline=_safe_float(row.get("raw_model_delta_vs_baseline")),
                raw_model_sign_acc_pct=_safe_float(row.get("raw_model_sign_acc_pct")),
                raw_model_direction_acc_pct=_safe_float(row.get("raw_model_direction_acc_pct")),
                raw_model_candidate_type=row.get("raw_model_candidate_type"),
                raw_model_used_before_guard=row.get("raw_model_used_before_guard"),
                guarded_candidate_type=row.get("guarded_candidate_type"),
                guarded_candidate_after_guard=row.get("guarded_candidate_after_guard"),
                recommendation_bucket=row.get("recommendation_bucket"),
            )
        )
    return entries


def get_dashboard_overview() -> DashboardOverviewDTO:
    registry = get_model_registry_entries()
    eligible_count = sum(1 for entry in registry if bool(entry.selection_eligibility))
    robust_count = sum(1 for entry in registry if _is_robust_status(entry.robustness_status))
    positive_delta_count = sum(1 for entry in registry if entry.delta_vs_baseline is not None and entry.delta_vs_baseline > 0)
    overfit_risk_count = sum(1 for entry in registry if str(entry.overfit_status or "").lower() in {"moderate", "severe"})
    suppressed_edge_count = sum(
        1
        for entry in registry
        if entry.raw_model_delta_vs_baseline is not None
        and entry.raw_model_delta_vs_baseline > 0
        and ((entry.delta_vs_baseline is None or entry.delta_vs_baseline <= 0) or not bool(entry.selection_eligibility))
    )
    main_model_key = next((entry.model_key for entry in registry if entry.is_main), None)
    return DashboardOverviewDTO(
        total_models=len(registry),
        eligible_count=eligible_count,
        robust_count=robust_count,
        positive_delta_count=positive_delta_count,
        overfit_risk_count=overfit_risk_count,
        suppressed_edge_count=suppressed_edge_count,
        main_model_key=main_model_key,
        registry=registry,
    )