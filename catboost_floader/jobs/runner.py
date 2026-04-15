from __future__ import annotations

import contextlib
import importlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Callable, Sequence

from catboost_floader.core.config import PROJECT_ROOT, REPORT_DIR, RUN_ALL_MODELS_EXECUTION_MODE
from catboost_floader.core.utils import get_logger, load_json, save_json
from catboost_floader.jobs.logs import append_job_log, ensure_job_log
from catboost_floader.jobs.registry import (
    create_job,
    mark_job_failed,
    mark_job_finished,
    mark_job_running,
    update_job,
)
from catboost_floader.jobs.status import JobRecord, utc_now_iso

try:  # pragma: no cover - optional runtime dependency
    psutil = importlib.import_module("psutil")
except Exception:  # pragma: no cover - optional runtime dependency
    psutil = None

logger = get_logger("jobs_runner")

_BACKGROUND_THREADS: dict[str, threading.Thread] = {}
_WORKSPACE_ROOT = str(Path(__file__).resolve().parents[2])
_PIPELINE_SUMMARY_PATH = os.path.join(REPORT_DIR, "pipeline_summary.json")


class _CpuUsageMonitor:
    def __init__(self, sample_interval_seconds: float = 0.5) -> None:
        self._sample_interval_seconds = max(0.1, float(sample_interval_seconds))
        self._samples: list[float] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if psutil is None:
            return
        psutil.cpu_percent(interval=None)
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="job-cpu-monitor",
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(self._sample_interval_seconds):
            try:
                self._samples.append(float(psutil.cpu_percent(interval=None)))
            except Exception:
                continue

    def stop(self) -> dict[str, float | None]:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._sample_interval_seconds + 0.2)
        if psutil is None:
            return {"avg_cpu_usage_percent": None, "max_cpu_usage_percent": None}
        if not self._samples:
            try:
                self._samples.append(float(psutil.cpu_percent(interval=None)))
            except Exception:
                pass
        if not self._samples:
            return {"avg_cpu_usage_percent": None, "max_cpu_usage_percent": None}
        return {
            "avg_cpu_usage_percent": float(sum(self._samples) / len(self._samples)),
            "max_cpu_usage_percent": float(max(self._samples)),
        }


def _is_run_all_models_job(action_type: str) -> bool:
    return str(action_type or "") == "run_all_models"


def _load_pipeline_summary_payload() -> dict[str, Any]:
    return dict(load_json(_PIPELINE_SUMMARY_PATH) or {})


def _derive_models_executed_count(pipeline_summary: dict[str, Any] | None) -> int:
    summary_payload = dict(pipeline_summary or {})
    return 1 + len(dict(summary_payload.get("multi_models", {}) or {}))


def _derive_execution_mode(pipeline_summary: dict[str, Any] | None) -> str:
    summary_payload = dict(pipeline_summary or {})
    summary_metrics = dict(summary_payload.get("execution_metrics", {}) or {})
    if summary_metrics.get("execution_mode"):
        return str(summary_metrics["execution_mode"])
    for model_summary in dict(summary_payload.get("multi_models", {}) or {}).values():
        if isinstance(model_summary, dict) and model_summary.get("execution_mode"):
            return str(model_summary["execution_mode"])
    return str(RUN_ALL_MODELS_EXECUTION_MODE)


def _build_run_all_execution_metrics(
    *,
    start_time: str | None,
    end_time: str | None,
    duration_seconds: float,
    avg_cpu_usage_percent: float | None,
    max_cpu_usage_percent: float | None,
    models_executed_count: int,
    execution_mode: str,
) -> dict[str, Any]:
    return {
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": float(max(0.0, duration_seconds)),
        "avg_cpu_usage_percent": avg_cpu_usage_percent,
        "max_cpu_usage_percent": max_cpu_usage_percent,
        "models_executed_count": max(0, int(models_executed_count)),
        "execution_mode": str(execution_mode),
    }


def _persist_run_all_execution_metrics(
    execution_metrics: dict[str, Any],
    *,
    pipeline_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary_payload = dict(pipeline_summary or _load_pipeline_summary_payload())
    existing_metrics = dict(summary_payload.get("execution_metrics", {}) or {})
    merged_metrics = dict(existing_metrics)
    merged_metrics.update(execution_metrics)
    merged_metrics["models_executed_count"] = int(
        merged_metrics.get("models_executed_count") or _derive_models_executed_count(summary_payload)
    )
    merged_metrics["execution_mode"] = str(
        merged_metrics.get("execution_mode") or _derive_execution_mode(summary_payload)
    )
    summary_payload["execution_metrics"] = merged_metrics
    save_json(summary_payload, _PIPELINE_SUMMARY_PATH)
    return merged_metrics


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
        args=(job.job_id, str(action_type), list(command)),
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
    action_type: str,
    command: Sequence[str],
    *,
    cwd: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> None:
    log_path = ensure_job_log(job_id, header_lines=[f"Launching subprocess: {' '.join(command)}"])
    process: subprocess.Popen[str] | None = None
    launch_cwd = cwd or _WORKSPACE_ROOT
    cpu_monitor: _CpuUsageMonitor | None = _CpuUsageMonitor() if _is_run_all_models_job(action_type) else None
    started_perf = time.perf_counter()
    started_at = utc_now_iso()
    try:
        running_job = mark_job_running(
            job_id,
            summary="Backend subprocess is running.",
            log_path=log_path,
            command=list(command),
        )
        if running_job is not None and running_job.started_at:
            started_at = str(running_job.started_at)
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
            if cpu_monitor is not None:
                cpu_monitor.start()
            return_code = process.wait()

        finished_at = utc_now_iso()
        execution_metrics = None
        if cpu_monitor is not None:
            cpu_usage = cpu_monitor.stop()
            pipeline_summary = _load_pipeline_summary_payload() if return_code == 0 else {}
            execution_metrics = _build_run_all_execution_metrics(
                start_time=started_at,
                end_time=finished_at,
                duration_seconds=time.perf_counter() - started_perf,
                avg_cpu_usage_percent=cpu_usage.get("avg_cpu_usage_percent"),
                max_cpu_usage_percent=cpu_usage.get("max_cpu_usage_percent"),
                models_executed_count=_derive_models_executed_count(pipeline_summary),
                execution_mode=_derive_execution_mode(pipeline_summary),
            )
            if return_code == 0:
                execution_metrics = _persist_run_all_execution_metrics(
                    execution_metrics,
                    pipeline_summary=pipeline_summary,
                )

        if return_code == 0:
            mark_job_finished(
                job_id,
                summary="Backend subprocess completed successfully.",
                result={
                    "return_code": return_code,
                    **({"execution_metrics": execution_metrics} if execution_metrics else {}),
                },
            )
        else:
            append_job_log(job_id, f"Process exited with code {return_code}.")
            mark_job_failed(
                job_id,
                error_message=f"Process exited with code {return_code}.",
                summary="Backend subprocess failed.",
                result={
                    "return_code": return_code,
                    **({"execution_metrics": execution_metrics} if execution_metrics else {}),
                },
            )
    except Exception as exc:
        if cpu_monitor is not None:
            cpu_monitor.stop()
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