import pandas as pd

from catboost_floader.frontend_api import action_requests, report_queries
from catboost_floader.frontend_api.dto import DashboardOverviewDTO, JobRecordDTO, ModelDetailDTO, ModelRegistryEntry
from catboost_floader.jobs.status import JobRecord, JobStatus
from frontend.services.reporting import choose_best_model


def test_choose_best_model_prefers_eligible_candidate():
    registry = pd.DataFrame(
        [
            {
                "model_key": "risk_high",
                "model_name": "Risk High",
                "selection_eligibility": False,
                "delta_vs_baseline": 4.0,
                "mean_delta_vs_baseline": 3.5,
                "sign_acc_pct": 57.0,
                "direction_acc_pct": 56.0,
            },
            {
                "model_key": "stable_main",
                "model_name": "Stable Main",
                "selection_eligibility": True,
                "delta_vs_baseline": 1.8,
                "mean_delta_vs_baseline": 1.6,
                "sign_acc_pct": 55.0,
                "direction_acc_pct": 54.0,
            },
        ]
    )

    best_model = choose_best_model(registry)

    assert best_model is not None
    assert best_model["model_key"] == "stable_main"


def test_build_dashboard_txt_report_includes_required_sections(monkeypatch):
    overview = DashboardOverviewDTO(
        total_models=2,
        eligible_count=1,
        robust_count=1,
        positive_delta_count=1,
        overfit_risk_count=1,
        suppressed_edge_count=1,
        main_model_key="main_direct_pipeline",
        registry=[
            ModelRegistryEntry(
                model_key="main_direct_pipeline",
                model_name="Main Pipeline",
                is_main=True,
                robustness_status="robust_winner",
                selection_eligibility=True,
                delta_vs_baseline=1.5,
                mean_delta_vs_baseline=1.2,
                sign_acc_pct=55.0,
                direction_acc_pct=54.0,
                overfit_status="none",
                recommendation_bucket="Preferred",
            ),
            ModelRegistryEntry(
                model_key="60min_3h",
                model_name="60min_3h",
                is_main=False,
                robustness_status="degraded",
                selection_eligibility=False,
                delta_vs_baseline=-0.3,
                mean_delta_vs_baseline=-0.1,
                sign_acc_pct=50.0,
                direction_acc_pct=49.0,
                overfit_status="moderate",
                overfit_reason="sign_gap_train_holdout_ge_0_07",
                raw_model_delta_vs_baseline=0.4,
                guarded_candidate_type="baseline_only",
                recommendation_bucket="Watch",
            ),
        ],
    )
    detail = ModelDetailDTO(
        model_key="main_direct_pipeline",
        model_name="Main Pipeline",
        is_main=True,
        summary={
            "MAE": 10.0,
            "delta_vs_baseline": 1.5,
            "robustness_status": "robust_winner",
            "selection_eligibility": True,
            "overfit_status": "none",
        },
        raw_model={},
        overfitting={
            "overfit_status": "none",
            "overfit_reason": "within_thresholds",
            "train_MAE": 8.0,
            "holdout_MAE": 10.0,
            "holdout_overfit_ratio": 1.05,
        },
        robustness={
            "mean_delta_vs_baseline": 1.2,
            "std_delta_vs_baseline": 0.4,
            "win_rate_vs_baseline": 0.8,
            "mean_sign_accuracy_pct": 55.0,
        },
        selection={
            "selected_candidate_type": "blend",
            "guarded_candidate_type": "blend",
            "raw_model_candidate_type": "model_only",
            "main_selection_final_ranking_reason": "mae_better",
            "final_holdout_guard_reason": "not_triggered",
        },
        registry={"recommendation_bucket": "Preferred"},
        artifact_paths={},
        artifacts={},
    )
    jobs = [
        JobRecordDTO(
            job_id="job-1",
            action_type="run_selected_model",
            label="Run selected model",
            status="running",
            created_at="2026-04-12 10:05:00 UTC",
            target_model="main_direct_pipeline",
            summary="Backend subprocess is running.",
            latest_log_lines=["starting...", "still running..."],
        )
    ]

    monkeypatch.setattr(report_queries, "get_dashboard_overview", lambda: overview)
    monkeypatch.setattr(report_queries, "get_model_detail", lambda model_key: detail)
    monkeypatch.setattr(report_queries, "get_recent_jobs", lambda **kwargs: jobs)
    monkeypatch.setattr(
        report_queries,
        "get_action_catalog",
        lambda selected_model_key: {
            "run_all_models": {"summary": "Queues the full backend pipeline as a tracked background job."},
            "run_selected_model": {"summary": "Queues a backend job for the focused model."},
            "refresh_artifacts": {"summary": "Records a refresh event and reloads artifacts."},
        },
    )
    monkeypatch.setattr(report_queries, "OUTPUT_DIR", "C:/tmp/outputs")
    monkeypatch.setattr(report_queries, "REPORT_DIR", "C:/tmp/outputs/reports")

    report = report_queries.build_dashboard_txt_report(
        selected_model_key="main_direct_pipeline",
        generated_at="2026-04-12 10:00:00 UTC",
    )

    assert "MODEL DASHBOARD REPORT" in report
    assert "Generated: 2026-04-12 10:00:00 UTC" in report
    assert "Best Model" in report
    assert "Main Pipeline" in report
    assert "Active / Eligible Models" in report
    assert "Key Metrics Table" in report
    assert "Overfit Summary" in report
    assert "Selected Model Details" in report
    assert "Selection Trace" in report
    assert "Practical Notes / Status" in report
    assert "Recent Job Status" in report
    assert "Run all models control" in report


def test_dispatch_action_request_queues_selected_model_job(monkeypatch):
    queued_job = JobRecord(
        job_id="job-selected-1",
        action_type="run_selected_model",
        status=JobStatus.QUEUED.value,
        created_at="2026-04-12T10:00:00+00:00",
        label="Run selected model",
        target_model="60min_3h",
        command=["python", "-m", "catboost_floader.app.job_entrypoints", "run-selected-model", "--model-key", "60min_3h"],
    )
    seen: dict[str, object] = {}

    def fake_submit_subprocess_job(**kwargs):
        seen.update(kwargs)
        return queued_job

    monkeypatch.setattr(action_requests, "submit_subprocess_job", fake_submit_subprocess_job)
    monkeypatch.setattr(
        action_requests,
        "get_job_status",
        lambda job_id, max_log_lines=0: JobRecordDTO(
            job_id=job_id,
            action_type="run_selected_model",
            label="Run selected model",
            status="queued",
            created_at="2026-04-12T10:00:00+00:00",
            target_model="60min_3h",
        ),
    )

    response = action_requests.dispatch_action_request("run_selected_model", "60min_3h")

    assert response.accepted is True
    assert response.job is not None
    assert response.job.job_id == "job-selected-1"
    assert seen["target_model"] == "60min_3h"
    assert seen["command"][-2:] == ["--model-key", "60min_3h"]