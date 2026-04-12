from __future__ import annotations

from typing import Any

from catboost_floader.frontend_api.dto import ActionResponseDTO
from catboost_floader.frontend_api.job_queries import get_job_status
from catboost_floader.jobs.runner import build_python_command, submit_callable_job, submit_subprocess_job

RUN_ALL_MODELS_COMMAND = build_python_command("catboost_floader.app.job_entrypoints", "run-all-models")


def get_action_catalog(selected_model_key: str | None) -> dict[str, dict[str, Any]]:
    model_key = str(selected_model_key or "main_direct_pipeline")
    selected_command = build_python_command(
        "catboost_floader.app.job_entrypoints",
        "run-selected-model",
        "--model-key",
        model_key,
    )
    return {
        "run_all_models": {
            "id": "run_all_models",
            "label": "Run all models",
            "mode": "job",
            "tone": "accent",
            "control_path": " ".join(RUN_ALL_MODELS_COMMAND),
            "summary": "Queues the full backend pipeline as a tracked background job.",
        },
        "run_selected_model": {
            "id": "run_selected_model",
            "label": "Run selected model",
            "mode": "job",
            "tone": "accent",
            "selected_model_key": model_key,
            "control_path": " ".join(selected_command),
            "summary": f"Queues a backend job for the focused model {model_key}.",
        },
        "refresh_artifacts": {
            "id": "refresh_artifacts",
            "label": "Refresh",
            "mode": "job",
            "tone": "success",
            "control_path": None,
            "summary": "Records a refresh event, then the UI clears cached artifact reads and reloads the current screen.",
        },
        "export_txt_report": {
            "id": "export_txt_report",
            "label": "Export TXT report",
            "mode": "query",
            "tone": "accent",
            "control_path": None,
            "summary": "Builds a plain-text report from the current backend dashboard state.",
        },
    }


def dispatch_action_request(action_id: str, selected_model_key: str | None = None) -> ActionResponseDTO:
    model_key = str(selected_model_key or "main_direct_pipeline")
    catalog = get_action_catalog(model_key)
    action = dict(catalog.get(action_id, {}) or {})
    if not action:
        return ActionResponseDTO(
            accepted=False,
            action_type=action_id,
            message="Unknown dashboard action.",
            tone="error",
        )

    if action_id == "run_all_models":
        job = submit_subprocess_job(
            action_type=action_id,
            label=action["label"],
            command=RUN_ALL_MODELS_COMMAND,
            summary="Queued full backend model run.",
        )
        return ActionResponseDTO(
            accepted=True,
            action_type=action_id,
            message="Full model run queued in the backend job runner.",
            tone="success",
            job=get_job_status(job.job_id, max_log_lines=0),
        )

    if action_id == "run_selected_model":
        command = build_python_command(
            "catboost_floader.app.job_entrypoints",
            "run-selected-model",
            "--model-key",
            model_key,
        )
        job = submit_subprocess_job(
            action_type=action_id,
            label=action["label"],
            command=command,
            target_model=model_key,
            summary=f"Queued backend run for {model_key}.",
        )
        return ActionResponseDTO(
            accepted=True,
            action_type=action_id,
            message=f"Selected model {model_key} queued in the backend job runner.",
            tone="success",
            job=get_job_status(job.job_id, max_log_lines=0),
        )

    if action_id == "refresh_artifacts":
        job = submit_callable_job(
            action_type=action_id,
            label=action["label"],
            target_model=model_key,
            summary="Queued artifact refresh event.",
            handler=lambda: _refresh_artifact_state(model_key),
        )
        return ActionResponseDTO(
            accepted=True,
            action_type=action_id,
            message="Artifact refresh recorded. The screen will reload current backend outputs.",
            tone="success",
            job=get_job_status(job.job_id, max_log_lines=0),
        )

    if action_id == "export_txt_report":
        from catboost_floader.frontend_api.report_queries import build_dashboard_txt_report

        return ActionResponseDTO(
            accepted=True,
            action_type=action_id,
            message="TXT report prepared from backend dashboard state.",
            tone="success",
            report_text=build_dashboard_txt_report(selected_model_key=model_key),
        )

    return ActionResponseDTO(
        accepted=False,
        action_type=action_id,
        message="Unsupported dashboard action.",
        tone="error",
    )


def _refresh_artifact_state(selected_model_key: str) -> dict[str, Any]:
    from catboost_floader.frontend_api.dashboard_queries import get_dashboard_overview
    from catboost_floader.frontend_api.model_detail_queries import get_model_detail

    overview = get_dashboard_overview()
    selected_model = get_model_detail(selected_model_key)
    return {
        "summary": f"Artifact state refreshed for {selected_model_key}.",
        "total_models": overview.total_models,
        "eligible_count": overview.eligible_count,
        "selected_model_name": selected_model.model_name if selected_model else selected_model_key,
    }