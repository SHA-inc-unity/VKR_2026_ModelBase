from __future__ import annotations

from typing import Iterable

from catboost_floader.frontend_api.dto import JobRecordDTO
from catboost_floader.jobs.logs import read_log_tail_from_path
from catboost_floader.jobs.registry import get_job, list_jobs
from catboost_floader.jobs.status import JobRecord


def _to_job_dto(job: JobRecord, *, max_log_lines: int = 0) -> JobRecordDTO:
    return JobRecordDTO(
        job_id=job.job_id,
        action_type=job.action_type,
        label=job.label,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        target_model=job.target_model,
        target_models=list(job.target_models),
        summary=job.summary,
        error_message=job.error_message,
        log_path=job.log_path,
        pid=job.pid,
        command=list(job.command),
        latest_log_lines=read_log_tail_from_path(job.log_path, max_lines=max_log_lines) if max_log_lines > 0 else [],
        result=dict(job.result),
    )


def get_recent_jobs(
    *,
    limit: int = 10,
    target_model: str | None = None,
    statuses: Iterable[str] | None = None,
    max_log_lines: int = 20,
) -> list[JobRecordDTO]:
    jobs = list_jobs(limit=limit, target_model=target_model, statuses=statuses)
    return [_to_job_dto(job, max_log_lines=max_log_lines) for job in jobs]


def get_job_status(job_id: str, *, max_log_lines: int = 40) -> JobRecordDTO | None:
    job = get_job(job_id)
    if job is None:
        return None
    return _to_job_dto(job, max_log_lines=max_log_lines)