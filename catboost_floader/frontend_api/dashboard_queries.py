from __future__ import annotations

from typing import Any

from catboost_floader.diagnostics.artifact_readers import load_pipeline_summary
from catboost_floader.diagnostics.model_snapshot import build_model_registry_rows

from catboost_floader.frontend_api.dto import DashboardOverviewDTO, ExecutionMetricsDTO, ModelRegistryEntry
from catboost_floader.frontend_api.job_queries import get_recent_jobs


def _safe_float(value: Any) -> float | None:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if value_f != value_f:
        return None
    return value_f


def _safe_int(value: Any) -> int | None:
    try:
        value_i = int(value)
    except (TypeError, ValueError):
        return None
    return value_i


def _safe_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _is_robust_status(status: Any) -> bool:
    return str(status or "").lower().startswith("robust")


def get_latest_execution_metrics() -> ExecutionMetricsDTO | None:
    pipeline_summary = dict(load_pipeline_summary() or {})
    summary_metrics = dict(pipeline_summary.get("execution_metrics", {}) or {})

    latest_job_metrics: dict[str, Any] = {}
    latest_finished_run = next(
        (
            job
            for job in get_recent_jobs(limit=25, statuses=["finished"], max_log_lines=0)
            if str(job.action_type) == "run_all_models" and str(job.status) == "finished"
        ),
        None,
    )
    if latest_finished_run is not None:
        latest_job_metrics = dict(latest_finished_run.result.get("execution_metrics", {}) or {})
        latest_job_metrics.setdefault("start_time", latest_finished_run.started_at)
        latest_job_metrics.setdefault("end_time", latest_finished_run.finished_at)

    models_executed_count = _safe_int(
        _first_present(
            summary_metrics.get("models_executed_count"),
            latest_job_metrics.get("models_executed_count"),
        )
    )
    if models_executed_count is None and pipeline_summary:
        models_executed_count = 1 + len(dict(pipeline_summary.get("multi_models", {}) or {}))

    payload = {
        "start_time": _safe_text(_first_present(summary_metrics.get("start_time"), latest_job_metrics.get("start_time"))),
        "end_time": _safe_text(_first_present(summary_metrics.get("end_time"), latest_job_metrics.get("end_time"))),
        "duration_seconds": _safe_float(_first_present(summary_metrics.get("duration_seconds"), latest_job_metrics.get("duration_seconds"))),
        "avg_cpu_usage_percent": _safe_float(_first_present(summary_metrics.get("avg_cpu_usage_percent"), latest_job_metrics.get("avg_cpu_usage_percent"))),
        "max_cpu_usage_percent": _safe_float(_first_present(summary_metrics.get("max_cpu_usage_percent"), latest_job_metrics.get("max_cpu_usage_percent"))),
        "models_executed_count": models_executed_count,
        "execution_mode": _safe_text(_first_present(summary_metrics.get("execution_mode"), latest_job_metrics.get("execution_mode"))),
    }

    if not any(value is not None for value in payload.values()):
        return None
    return ExecutionMetricsDTO(**payload)


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
                sign_tp=_safe_int(row.get("sign_tp")),
                sign_tn=_safe_int(row.get("sign_tn")),
                sign_fp=_safe_int(row.get("sign_fp")),
                sign_fn=_safe_int(row.get("sign_fn")),
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
        execution_metrics=get_latest_execution_metrics(),
        registry=registry,
    )