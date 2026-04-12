from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import traceback
from typing import Any, Callable, Sequence

from catboost_floader.core.config import PROJECT_ROOT
from catboost_floader.core.utils import get_logger
from catboost_floader.jobs.logs import append_job_log, ensure_job_log
from catboost_floader.jobs.registry import (
    create_job,
    mark_job_failed,
    mark_job_finished,
    mark_job_running,
    update_job,
)
from catboost_floader.jobs.status import JobRecord

logger = get_logger("jobs_runner")

_BACKGROUND_THREADS: dict[str, threading.Thread] = {}
_WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])


def build_python_command(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def _build_launch_env(*, env_overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    pythonpath_parts = [_WORKSPACE_ROOT]
    existing_pythonpath = str(env.get("PYTHONPATH", "")).strip()
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(part for part in pythonpath_parts if part)
    if env_overrides:
        env.update(env_overrides)
    return env


def submit_subprocess_job(
    *,
    action_type: str,
    label: str,
    command: Sequence[str],
    target_model: str | None = None,
    target_models: Sequence[str] | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    cwd: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> JobRecord:
    job = create_job(
        action_type=action_type,
        label=label,
        target_model=target_model,
        target_models=list(target_models or []),
        summary=summary or "Queued backend subprocess job.",
        command=list(command),
        metadata=metadata,
    )
    thread = threading.Thread(
        target=_run_subprocess_job,
        args=(job.job_id, list(command)),
        kwargs={"cwd": cwd, "env_overrides": env_overrides},
        daemon=True,
        name=f"job-subprocess-{job.job_id}",
    )
    _BACKGROUND_THREADS[job.job_id] = thread
    thread.start()
    return job


def submit_callable_job(
    *,
    action_type: str,
    label: str,
    handler: Callable[[], dict[str, Any] | None],
    target_model: str | None = None,
    target_models: Sequence[str] | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> JobRecord:
    job = create_job(
        action_type=action_type,
        label=label,
        target_model=target_model,
        target_models=list(target_models or []),
        summary=summary or "Queued backend callable job.",
        metadata=metadata,
    )
    thread = threading.Thread(
        target=_run_callable_job,
        args=(job.job_id, handler),
        daemon=True,
        name=f"job-callable-{job.job_id}",
    )
    _BACKGROUND_THREADS[job.job_id] = thread
    thread.start()
    return job


def _run_subprocess_job(
    job_id: str,
    command: Sequence[str],
    *,
    cwd: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> None:
    log_path = ensure_job_log(job_id, header_lines=[f"Launching subprocess: {' '.join(command)}"])
    process: subprocess.Popen[str] | None = None
    launch_cwd = cwd or _WORKSPACE_ROOT
    try:
        mark_job_running(
            job_id,
            summary="Backend subprocess is running.",
            log_path=log_path,
            command=list(command),
        )
        env = _build_launch_env(env_overrides=env_overrides)

        with open(log_path, "a", encoding="utf-8") as log_handle:
            log_handle.write(f"[runner] cwd={launch_cwd}\n")
            log_handle.write(f"[runner] workspace_root={_WORKSPACE_ROOT}\n")
            process = subprocess.Popen(
                list(command),
                cwd=launch_cwd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            update_job(job_id, pid=process.pid)
            return_code = process.wait()

        if return_code == 0:
            mark_job_finished(
                job_id,
                summary="Backend subprocess completed successfully.",
                result={"return_code": return_code},
            )
        else:
            append_job_log(job_id, f"Process exited with code {return_code}.")
            mark_job_failed(
                job_id,
                error_message=f"Process exited with code {return_code}.",
                summary="Backend subprocess failed.",
                result={"return_code": return_code},
            )
    except Exception as exc:
        append_job_log(job_id, f"Runner exception: {exc}")
        append_job_log(job_id, traceback.format_exc().rstrip())
        mark_job_failed(
            job_id,
            error_message=str(exc),
            summary="Backend subprocess failed before completion.",
            result={"command": list(command)},
        )
        logger.exception("Subprocess job failed: %s", job_id)
    finally:
        _BACKGROUND_THREADS.pop(job_id, None)


def _run_callable_job(job_id: str, handler: Callable[[], dict[str, Any] | None]) -> None:
    log_path = ensure_job_log(job_id, header_lines=["Launching callable job."])
    try:
        mark_job_running(job_id, summary="Backend callable is running.", log_path=log_path)
        with open(log_path, "a", encoding="utf-8") as log_handle:
            with contextlib.redirect_stdout(log_handle), contextlib.redirect_stderr(log_handle):
                result = handler() or {}
        summary = None
        if isinstance(result, dict):
            summary = result.get("summary")
        mark_job_finished(
            job_id,
            summary=str(summary or "Backend callable completed successfully."),
            result=dict(result or {}),
        )
    except Exception as exc:
        append_job_log(job_id, f"Callable exception: {exc}")
        append_job_log(job_id, traceback.format_exc().rstrip())
        mark_job_failed(
            job_id,
            error_message=str(exc),
            summary="Backend callable failed.",
        )
        logger.exception("Callable job failed: %s", job_id)
    finally:
        _BACKGROUND_THREADS.pop(job_id, None)