from pathlib import Path

from catboost_floader.jobs import logs, registry, runner
from catboost_floader.jobs.status import JobStatus


def _patch_job_dirs(monkeypatch, tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    monkeypatch.setattr(registry, "JOBS_DIR", str(jobs_root))
    monkeypatch.setattr(registry, "JOBS_REGISTRY_DIR", str(jobs_root / "registry"))
    monkeypatch.setattr(logs, "JOBS_DIR", str(jobs_root))
    monkeypatch.setattr(logs, "JOB_LOG_DIR", str(jobs_root / "logs"))


def test_job_registry_persists_status_and_logs(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)

    job = registry.create_job(
        action_type="refresh_artifacts",
        label="Refresh",
        target_model="main_direct_pipeline",
        summary="Queued artifact refresh.",
    )
    log_path = logs.ensure_job_log(job.job_id)
    logs.append_job_log(job.job_id, "artifact refresh complete")
    registry.mark_job_running(job.job_id, log_path=log_path, summary="Refreshing backend state.")
    registry.mark_job_finished(job.job_id, summary="Refresh complete.", result={"rows": 2})

    stored = registry.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FINISHED.value
    assert stored.result["rows"] == 2

    tail = logs.read_job_log_tail(job.job_id, max_lines=4)
    assert any("artifact refresh complete" in line for line in tail)

    listed = registry.list_jobs(limit=1)
    assert listed[0].job_id == job.job_id


def test_run_subprocess_job_updates_registry(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)

    job = registry.create_job(
        action_type="run_selected_model",
        label="Run selected model",
        target_model="60min_3h",
        command=["python", "-m", "example"],
    )

    class FakePopen:
        def __init__(self, command, cwd, stdout, stderr, text, env):
            self.pid = 4242
            stdout.write("hello from fake job\n")
            stdout.flush()

        def wait(self):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)

    runner._run_subprocess_job(job.job_id, ["python", "-m", "example"], cwd=str(tmp_path))

    stored = registry.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FINISHED.value
    assert stored.pid == 4242
    assert any("hello from fake job" in line for line in logs.read_job_log_tail(job.job_id, max_lines=10))


def test_run_subprocess_job_defaults_to_workspace_root(monkeypatch, tmp_path):
    _patch_job_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_WORKSPACE_ROOT", str(tmp_path / "workspace-root"))

    job = registry.create_job(
        action_type="run_all_models",
        label="Run all models",
        command=["python", "-m", "catboost_floader.app.job_entrypoints", "run-all-models"],
    )
    seen: dict[str, object] = {}

    class FakePopen:
        def __init__(self, command, cwd, stdout, stderr, text, env):
            seen["cwd"] = cwd
            seen["pythonpath"] = env.get("PYTHONPATH")
            self.pid = 1010
            stdout.write("module launch ok\n")
            stdout.flush()

        def wait(self):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)

    runner._run_subprocess_job(job.job_id, ["python", "-m", "catboost_floader.app.job_entrypoints", "run-all-models"])

    stored = registry.get_job(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FINISHED.value
    assert seen["cwd"] == str(tmp_path / "workspace-root")
    assert str(tmp_path / "workspace-root") in str(seen["pythonpath"])