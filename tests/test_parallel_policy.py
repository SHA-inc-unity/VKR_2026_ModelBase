from catboost_floader.core import parallel_policy


def _patch_total_threads(monkeypatch, total_threads: int) -> None:
    monkeypatch.setattr(parallel_policy, "CPU_LOGICAL_THREADS", total_threads)
    monkeypatch.setattr(parallel_policy, "CPU_PARALLEL_TARGET_THREADS", total_threads)
    monkeypatch.setattr(parallel_policy, "CPU_PARALLEL_MAX_THREADS", total_threads)
    monkeypatch.setitem(
        parallel_policy._DEFAULT_STAGE_POLICY["backtest"],
        "preferred_inner_threads",
        total_threads,
    )


def test_fast_screening_policy_uses_full_host_budget(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)

    policy = parallel_policy.resolve_cpu_stage_parallel_policy(
        "fast_screening",
        parallel_units=256,
        allow_parallel=True,
    )

    assert policy["total_threads"] == 64
    assert policy["parallel_enabled"] is True
    assert policy["outer_workers"] > 1
    assert policy["inner_threads"] == policy["catboost_thread_count"]
    assert policy["expected_cpu_budget"] == policy["outer_workers"] * policy["inner_threads"]
    assert abs(policy["expected_cpu_budget"] - 64) <= max(1, policy["outer_workers"])


def test_nested_outer_worker_disables_additional_outer_parallel(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)

    policy = parallel_policy.resolve_cpu_stage_parallel_policy(
        "stage2_full_evaluation",
        parallel_units=64,
        nested_outer_parallel=True,
        nested_thread_count=4,
        allow_parallel=True,
    )

    assert policy["parallel_enabled"] is False
    assert policy["outer_workers"] == 1
    assert policy["inner_threads"] == 4
    assert policy["nested_parallel"] is False
    assert policy["expected_cpu_budget"] == 4


def test_backtest_policy_keeps_single_outer_worker(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)

    policy = parallel_policy.resolve_cpu_stage_parallel_policy(
        "backtest",
        parallel_units=1,
        allow_parallel=True,
    )

    assert policy["outer_workers"] == 1
    assert policy["inner_threads"] == 64
    assert policy["expected_cpu_budget"] == 64
    assert policy["executor_kind"] == "sequential"


def test_unbounded_multi_model_mode_uses_one_worker_per_model(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)

    policy = parallel_policy.resolve_cpu_stage_parallel_policy(
        "multi_model_evaluation",
        parallel_units=12,
        allow_parallel=True,
        execution_mode=parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED,
    )

    assert policy["execution_mode"] == parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED
    assert policy["parallel_enabled"] is True
    assert policy["outer_workers"] == 12
    assert policy["catboost_thread_count"] is None
    assert policy["worker_thread_limits_applied"] is False
    assert policy["nested_thread_caps_applied"] is False


def test_apply_cpu_worker_limits_is_noop_in_unbounded_mode(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)
    monkeypatch.setenv("OMP_NUM_THREADS", "99")
    monkeypatch.setenv("CATBOOST_WORKER_THREADS", "99")
    monkeypatch.setenv("CATBOOST_OUTER_PARALLEL", "1")

    applied_threads = parallel_policy.apply_cpu_worker_limits(
        8,
        mark_outer_parallel=True,
        execution_mode=parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED,
    )

    assert applied_threads is None
    assert parallel_policy.current_worker_thread_count(parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED) is None
    assert parallel_policy.is_nested_outer_parallel(parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED) is False
    assert parallel_policy.get_cpu_execution_mode_metadata(parallel_policy.CPU_EXECUTION_MODE_UNBOUNDED) == {
        "execution_mode": "unbounded",
        "worker_thread_limits_applied": False,
        "nested_thread_caps_applied": False,
    }


def test_policy_log_includes_required_budget_fields(monkeypatch) -> None:
    _patch_total_threads(monkeypatch, 64)

    policy = parallel_policy.resolve_stage2_parallel_policy(8, 4)
    log_line = parallel_policy.format_cpu_stage_policy_log(policy)

    assert "execution_mode=" in log_line
    assert "worker_thread_limits_applied=" in log_line
    assert "nested_thread_caps_applied=" in log_line
    assert "total_threads=" in log_line
    assert "outer_workers=" in log_line
    assert "inner_threads=" in log_line
    assert "nested_parallel=" in log_line
    assert "parallel_units=" in log_line
    assert "expected_cpu_budget=" in log_line