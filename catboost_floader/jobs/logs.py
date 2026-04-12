from __future__ import annotations

import os
from collections import deque
from typing import Iterable

from catboost_floader.core.config import OUTPUT_DIR
from catboost_floader.core.utils import ensure_dirs
from catboost_floader.jobs.status import utc_now_iso

JOBS_DIR = os.path.join(OUTPUT_DIR, "jobs")
JOB_LOG_DIR = os.path.join(JOBS_DIR, "logs")


def job_log_path(job_id: str) -> str:
    return os.path.join(JOB_LOG_DIR, f"{job_id}.log")


def ensure_job_log(job_id: str, *, header_lines: Iterable[str] | None = None) -> str:
    ensure_dirs([JOB_LOG_DIR])
    path = job_log_path(job_id)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"[{utc_now_iso()}] Log created for job {job_id}\n")
            for line in header_lines or []:
                handle.write(f"[{utc_now_iso()}] {line}\n")
    return path


def append_job_log(job_id: str, message: str) -> str:
    path = ensure_job_log(job_id)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"[{utc_now_iso()}] {message.rstrip()}\n")
    return path


def read_log_tail_from_path(log_path: str | None, *, max_lines: int = 80) -> list[str]:
    if not log_path or not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=max_lines)]


def read_job_log_tail(job_id: str, *, max_lines: int = 80) -> list[str]:
    return read_log_tail_from_path(job_log_path(job_id), max_lines=max_lines)