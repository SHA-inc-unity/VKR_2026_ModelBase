from catboost_floader.frontend_api import dashboard_queries
from catboost_floader.frontend_api.dto import JobRecordDTO


def test_get_dashboard_overview_exposes_execution_metrics_from_pipeline_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_queries,
        "build_model_registry_rows",
        lambda: [
            {
                "model_key": "main_direct_pipeline",
                "model_name": "Main Pipeline",
                "is_main": True,
                "selection_eligibility": True,
                "robustness_status": "robust_winner",
                "delta_vs_baseline": 1.2,
            }
        ],
    )
    monkeypatch.setattr(
        dashboard_queries,
        "load_pipeline_summary",
        lambda: {
            "multi_models": {"60min_3h": {}, "15min_6h": {}},
            "execution_metrics": {
                "start_time": "2026-04-12T10:00:00+00:00",
                "end_time": "2026-04-12T10:12:30+00:00",
                "duration_seconds": 750.0,
                "avg_cpu_usage_percent": 81.5,
                "max_cpu_usage_percent": 96.2,
                "models_executed_count": 3,
                "execution_mode": "unbounded",
            },
        },
    )
    monkeypatch.setattr(dashboard_queries, "get_recent_jobs", lambda **kwargs: [])

    overview = dashboard_queries.get_dashboard_overview()

    assert overview.execution_metrics is not None
    assert overview.execution_metrics.duration_seconds == 750.0
    assert overview.execution_metrics.avg_cpu_usage_percent == 81.5
    assert overview.execution_metrics.max_cpu_usage_percent == 96.2
    assert overview.execution_metrics.models_executed_count == 3
    assert overview.execution_metrics.execution_mode == "unbounded"


def test_get_dashboard_overview_falls_back_to_latest_finished_run_all_job(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_queries, "build_model_registry_rows", lambda: [])
    monkeypatch.setattr(dashboard_queries, "load_pipeline_summary", lambda: {})
    monkeypatch.setattr(
        dashboard_queries,
        "get_recent_jobs",
        lambda **kwargs: [
            JobRecordDTO(
                job_id="job-1",
                action_type="run_all_models",
                label="Run all models",
                status="finished",
                created_at="2026-04-12T10:00:00+00:00",
                started_at="2026-04-12T10:01:00+00:00",
                finished_at="2026-04-12T10:05:00+00:00",
                result={
                    "execution_metrics": {
                        "duration_seconds": 240.0,
                        "avg_cpu_usage_percent": 72.0,
                        "max_cpu_usage_percent": 88.0,
                        "models_executed_count": 5,
                        "execution_mode": "unbounded",
                    }
                },
            )
        ],
    )

    overview = dashboard_queries.get_dashboard_overview()

    assert overview.execution_metrics is not None
    assert overview.execution_metrics.start_time == "2026-04-12T10:01:00+00:00"
    assert overview.execution_metrics.end_time == "2026-04-12T10:05:00+00:00"
    assert overview.execution_metrics.duration_seconds == 240.0
    assert overview.execution_metrics.models_executed_count == 5


def test_get_dashboard_overview_exposes_sign_confusion_fields_on_registry_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        dashboard_queries,
        "build_model_registry_rows",
        lambda: [
            {
                "model_key": "main_direct_pipeline",
                "model_name": "Main Pipeline",
                "is_main": True,
                "selection_eligibility": True,
                "robustness_status": "robust_winner",
                "delta_vs_baseline": 1.2,
                "sign_acc_pct": 55.0,
                "sign_tp": 14,
                "sign_tn": 13,
                "sign_fp": 6,
                "sign_fn": 5,
            }
        ],
    )
    monkeypatch.setattr(dashboard_queries, "load_pipeline_summary", lambda: {})
    monkeypatch.setattr(dashboard_queries, "get_recent_jobs", lambda **kwargs: [])

    overview = dashboard_queries.get_dashboard_overview()

    assert len(overview.registry) == 1
    assert overview.registry[0].sign_tp == 14
    assert overview.registry[0].sign_tn == 13
    assert overview.registry[0].sign_fp == 6
    assert overview.registry[0].sign_fn == 5