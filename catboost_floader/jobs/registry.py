from __future__ import annotations

import os
import threading
from typing import Any, Iterable
from uuid import uuid4

from catboost_floader.core.config import OUTPUT_DIR
from catboost_floader.core.utils import ensure_dirs, load_json, save_json
from catboost_floader.jobs.status import JobRecord, JobStatus, utc_now_iso

JOBS_DIR = os.path.join(OUTPUT_DIR, "jobs")
JOBS_REGISTRY_DIR = os.path.join(JOBS_DIR, "registry")

_REGISTRY_LOCK = threading.Lock()


def ensure_job_storage() -> None:
    ensure_dirs([JOBS_DIR, JOBS_REGISTRY_DIR])


def _job_file_path(job_id: str) -> str:
    return os.path.join(JOBS_REGISTRY_DIR, f"{job_id}.json")


def _make_job_id() -> str:
    return f"{utc_now_iso().replace(':', '').replace('-', '').replace('+00:00', 'Z').replace('T', '_')}_{uuid4().hex[:8]}"


def _write_job(job: JobRecord) -> JobRecord:
    ensure_job_storage()
    save_json(job.to_dict(), _job_file_path(job.job_id))
    return job


def create_job(
    *,
    action_type: str,
    label: str,
    target_model: str | None = None,
    target_models: Iterable[str] | None = None,
    summary: str | None = None,
    command: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    log_path: str | None = None,
    result: dict[str, Any] | None = None,
) -> JobRecord:
    with _REGISTRY_LOCK:
        job = JobRecord(
            job_id=_make_job_id(),
            action_type=str(action_type),
            status=JobStatus.QUEUED.value,
            created_at=utc_now_iso(),
            label=str(label),
            target_model=target_model,
            target_models=list(target_models or []),
            summary=summary,
            command=list(command or []),
            metadata=dict(metadata or {}),
            log_path=log_path,
            result=dict(result or {}),
        )
        return _write_job(job)


def get_job(job_id: str) -> JobRecord | None:
    payload = load_json(_job_file_path(job_id))
    if not payload:
        return None
    return JobRecord.from_dict(payload)


def update_job(job_id: str, **changes: Any) -> JobRecord | None:
    with _REGISTRY_LOCK:
        existing = get_job(job_id)
        if existing is None:
            return None
        payload = existing.to_dict()
        for key, value in changes.items():
            if value is None and key not in {"error_message", "finished_at", "started_at", "pid"}:
                continue
            if key == "status" and isinstance(value, JobStatus):
                payload[key] = value.value
            else:
                payload[key] = value
        updated = JobRecord.from_dict(payload)
        return _write_job(updated)


def mark_job_running(
    job_id: str,
    *,
    summary: str | None = None,
    log_path: str | None = None,
    pid: int | None = None,
    command: list[str] | None = None,
) -> JobRecord | None:
    return update_job(
        job_id,
        status=JobStatus.RUNNING.value,
        started_at=utc_now_iso(),
        finished_at=None,
        summary=summary,
        log_path=log_path,
        pid=pid,
        command=list(command or []),
        error_message=None,
    )


def mark_job_finished(
    job_id: str,
    *,
    summary: str | None = None,
    result: dict[str, Any] | None = None,
) -> JobRecord | None:
    return update_job(
        job_id,
        status=JobStatus.FINISHED.value,
        finished_at=utc_now_iso(),
        summary=summary,
        result=dict(result or {}),
        error_message=None,
    )


def mark_job_failed(
    job_id: str,
    *,
    error_message: str,
    summary: str | None = None,
    result: dict[str, Any] | None = None,
) -> JobRecord | None:
    return update_job(
        job_id,
        status=JobStatus.FAILED.value,
        finished_at=utc_now_iso(),
        summary=summary or "Job failed.",
        error_message=error_message,
        result=dict(result or {}),
    )


def list_jobs(
    *,
    limit: int | None = 20,
    action_type: str | None = None,
    target_model: str | None = None,
    statuses: Iterable[str] | None = None,
) -> list[JobRecord]:
    ensure_job_storage()
    normalized_statuses = {str(status) for status in (statuses or [])}
    jobs: list[JobRecord] = []
    for file_name in os.listdir(JOBS_REGISTRY_DIR):
        if not file_name.endswith(".json"):
            continue
        payload = load_json(os.path.join(JOBS_REGISTRY_DIR, file_name))
        if not payload:
            continue
        job = JobRecord.from_dict(payload)
        if action_type and job.action_type != action_type:
            continue
        if target_model and target_model not in {job.target_model, *job.target_models}:
            continue
        if normalized_statuses and job.status not in normalized_statuses:
            continue
        jobs.append(job)
    jobs.sort(key=lambda item: item.created_at, reverse=True)
    if limit is not None:
        return jobs[:limit]
    return jobs