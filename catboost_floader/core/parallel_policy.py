from __future__ import annotations

import math
import os
from typing import Any

CPU_WORKER_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)

CPU_EXECUTION_MODE_ENV_VAR = "MODELLINE_CPU_EXECUTION_MODE"
CPU_WORKER_LIMITS_APPLIED_ENV_VAR = "MODELLINE_WORKER_THREAD_LIMITS_APPLIED"
CPU_NESTED_THREAD_CAPS_APPLIED_ENV_VAR = "MODELLINE_NESTED_THREAD_CAPS_APPLIED"

CPU_EXECUTION_MODE_ADAPTIVE = "adaptive"
CPU_EXECUTION_MODE_UNBOUNDED = "unbounded"
_VALID_CPU_EXECUTION_MODES = {
    CPU_EXECUTION_MODE_ADAPTIVE,
    CPU_EXECUTION_MODE_UNBOUNDED,
}

CPU_LOGICAL_THREADS = max(1, os.cpu_count() or 1)
CPU_PARALLEL_TARGET_THREADS = CPU_LOGICAL_THREADS
CPU_PARALLEL_MAX_THREADS = CPU_PARALLEL_TARGET_THREADS
CPU_PARALLEL_ENABLE_NESTED_PARALLEL = True
MAX_CPU_UTILIZATION_MODE = CPU_EXECUTION_MODE_ADAPTIVE

ENABLE_PARALLEL_CPU_FAST_SCREENING = True
ENABLE_PARALLEL_CPU_FULL_EVALUATION = True
ENABLE_PARALLEL_CPU_BACKTEST = True
ENABLE_PARALLEL_CPU_BACKTEST_WINDOW = True
ENABLE_PARALLEL_CPU_MULTI_MODEL = True

STAGE2_PARALLEL_MODE = "adaptive_cpu"
STAGE2_PARALLEL_GRANULARITY = "candidate_fold"

RUN_ALL_MODELS_CPU_MODE = str(
    os.environ.get("RUN_ALL_MODELS_CPU_MODE", CPU_EXECUTION_MODE_UNBOUNDED)
).strip().lower()
if RUN_ALL_MODELS_CPU_MODE not in _VALID_CPU_EXECUTION_MODES:
    RUN_ALL_MODELS_CPU_MODE = CPU_EXECUTION_MODE_UNBOUNDED

_DEFAULT_STAGE_POLICY: dict[str, dict[str, Any]] = {
    "fast_screening": {
        "granularity": "candidate",
        "preferred_inner_threads": 4,
        "min_inner_threads": 2,
        "allow_nested_parallel": True,
        "outer_parallel_allowed": True,
        "preferred_executor": "process",
    },
    "stage2_full_evaluation": {
        "granularity": "candidate_fold",
        "preferred_inner_threads": 4,
        "min_inner_threads": 2,
        "allow_nested_parallel": True,
        "outer_parallel_allowed": True,
        "preferred_executor": "process",
    },
    "backtest": {
        "granularity": "vectorized_batch",
        "preferred_inner_threads": CPU_PARALLEL_TARGET_THREADS,
        "min_inner_threads": 1,
        "allow_nested_parallel": False,
        "outer_parallel_allowed": False,
        "preferred_executor": "sequential",
    },
    "backtest_window_evaluation": {
        "granularity": "backtest_window",
        "preferred_inner_threads": 2,
        "min_inner_threads": 1,
        "allow_nested_parallel": True,
        "outer_parallel_allowed": True,
        "preferred_executor": "process",
    },
    "multi_model_evaluation": {
        "granularity": "model_key",
        "preferred_inner_threads": 4,
        "min_inner_threads": 2,
        "allow_nested_parallel": True,
        "outer_parallel_allowed": True,
        "preferred_executor": "process",
    },
}


def get_total_logical_threads() -> int:
    return CPU_LOGICAL_THREADS


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        coerced = int(default)
    return max(1, coerced)


def normalize_cpu_execution_mode(
    mode: Any,
    *,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> str:
    normalized_default = str(default or CPU_EXECUTION_MODE_ADAPTIVE).strip().lower()
    if normalized_default not in _VALID_CPU_EXECUTION_MODES:
        normalized_default = CPU_EXECUTION_MODE_ADAPTIVE
    normalized_mode = str(mode or normalized_default).strip().lower()
    if normalized_mode not in _VALID_CPU_EXECUTION_MODES:
        return normalized_default
    return normalized_mode


def _resolve_execution_mode(
    execution_mode: str | None = None,
    *,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> str:
    if execution_mode is None:
        execution_mode = os.environ.get(CPU_EXECUTION_MODE_ENV_VAR)
    return normalize_cpu_execution_mode(execution_mode, default=default)


def get_active_cpu_execution_mode(
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> str:
    return _resolve_execution_mode(default=default)


def is_unbounded_cpu_execution_mode(
    execution_mode: str | None = None,
    *,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> bool:
    return _resolve_execution_mode(execution_mode, default=default) == CPU_EXECUTION_MODE_UNBOUNDED


def should_apply_worker_thread_limits(
    execution_mode: str | None = None,
    *,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> bool:
    return not is_unbounded_cpu_execution_mode(execution_mode, default=default)


def should_apply_nested_thread_caps(
    execution_mode: str | None = None,
    *,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> bool:
    return not is_unbounded_cpu_execution_mode(execution_mode, default=default)


def clear_cpu_worker_limits() -> None:
    for env_var in CPU_WORKER_THREAD_ENV_VARS:
        os.environ.pop(env_var, None)
    os.environ.pop("CATBOOST_WORKER_THREADS", None)


def activate_cpu_execution_mode(
    execution_mode: str | None = None,
    *,
    mark_outer_parallel: bool = False,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> str:
    normalized_mode = _resolve_execution_mode(execution_mode, default=default)
    worker_thread_limits_applied = should_apply_worker_thread_limits(normalized_mode)
    nested_thread_caps_applied = should_apply_nested_thread_caps(normalized_mode)

    os.environ[CPU_EXECUTION_MODE_ENV_VAR] = normalized_mode
    os.environ[CPU_WORKER_LIMITS_APPLIED_ENV_VAR] = "1" if worker_thread_limits_applied else "0"
    os.environ[CPU_NESTED_THREAD_CAPS_APPLIED_ENV_VAR] = "1" if nested_thread_caps_applied else "0"
    if not worker_thread_limits_applied:
        clear_cpu_worker_limits()
    os.environ["CATBOOST_OUTER_PARALLEL"] = "1" if mark_outer_parallel and nested_thread_caps_applied else "0"
    return normalized_mode


def get_cpu_execution_mode_metadata(
    execution_mode: str | None = None,
    *,
    model_workers: int | None = None,
    default: str = CPU_EXECUTION_MODE_ADAPTIVE,
) -> dict[str, Any]:
    normalized_mode = _resolve_execution_mode(execution_mode, default=default)
    metadata = {
        "execution_mode": normalized_mode,
        "worker_thread_limits_applied": should_apply_worker_thread_limits(normalized_mode),
        "nested_thread_caps_applied": should_apply_nested_thread_caps(normalized_mode),
    }
    if model_workers is not None:
        metadata["model_workers"] = max(1, int(model_workers))
    return metadata


def _resolve_stage_defaults(stage: str) -> tuple[str, dict[str, Any]]:
    stage_key = str(stage or "").strip().lower()
    if stage_key not in _DEFAULT_STAGE_POLICY:
        stage_key = "stage2_full_evaluation"
    return stage_key, dict(_DEFAULT_STAGE_POLICY[stage_key])


def _select_parallel_layout(
    *,
    parallel_units: int,
    target_threads: int,
    preferred_inner_threads: int,
    min_inner_threads: int,
    max_outer_workers: int,
    configured_inner_threads: int | None,
) -> tuple[int, int]:
    max_outer = max(1, min(parallel_units, max_outer_workers, target_threads))
    best_layout: tuple[tuple[int, int, int, int], int, int] | None = None

    for outer_workers in range(1, max_outer + 1):
        if configured_inner_threads is not None:
            inner_candidates = {configured_inner_threads}
        else:
            inner_candidates = {
                max(1, int(round(target_threads / outer_workers))),
                max(1, target_threads // outer_workers),
                max(1, math.ceil(target_threads / outer_workers)),
            }

        for inner_threads in inner_candidates:
            if outer_workers > 1 and inner_threads < min_inner_threads:
                continue

            cpu_budget = outer_workers * inner_threads
            score = (
                abs(target_threads - cpu_budget),
                0 if cpu_budget <= target_threads else 1,
                abs(preferred_inner_threads - inner_threads),
                -outer_workers,
            )
            candidate = (score, outer_workers, inner_threads)
            if best_layout is None or candidate[0] < best_layout[0]:
                best_layout = candidate

    if best_layout is None:
        return 1, max(1, target_threads)
    return best_layout[1], best_layout[2]


def _build_unbounded_policy(
    *,
    stage_key: str,
    parallel_units: int,
    granularity: str,
    allow_parallel: bool,
    configured_target_threads: int | None,
) -> dict[str, Any]:
    total_threads = get_total_logical_threads()
    target_requested = _coerce_positive_int(configured_target_threads, total_threads)
    target_threads = min(total_threads, target_requested)
    model_parallel_enabled = bool(
        stage_key == "multi_model_evaluation" and allow_parallel and parallel_units > 1
    )
    outer_workers = parallel_units if model_parallel_enabled else 1
    executor_kind = "process" if model_parallel_enabled else "sequential"
    expected_cpu_budget = outer_workers * total_threads if model_parallel_enabled else total_threads

    return {
        "stage": stage_key,
        "mode": "unbounded_parallel_cpu" if model_parallel_enabled else "unbounded_cpu",
        "execution_mode": CPU_EXECUTION_MODE_UNBOUNDED,
        "parallel_enabled": model_parallel_enabled,
        "granularity": granularity,
        "nested_parallel": False,
        "nested_enabled": False,
        "executor_kind": executor_kind,
        "target_threads_requested": target_requested,
        "target_threads": target_threads,
        "total_threads": total_threads,
        "host_threads": total_threads,
        "outer_workers": outer_workers,
        "inner_threads": total_threads,
        "parallel_units": parallel_units,
        "expected_cpu_budget": expected_cpu_budget,
        "estimated_cpu_budget": expected_cpu_budget,
        "catboost_thread_count": None,
        "worker_thread_limits_applied": False,
        "nested_thread_caps_applied": False,
        "fallback_reasons": ["unbounded_mode_bypasses_cpu_budgeting"],
        "fallback_reason": "unbounded_mode_bypasses_cpu_budgeting",
        "full_target_reached": True,
    }


def resolve_cpu_stage_parallel_policy(
    stage: str,
    *,
    parallel_units: int,
    granularity: str | None = None,
    nested_outer_parallel: bool = False,
    nested_thread_count: int | None = None,
    allow_parallel: bool = True,
    configured_outer_workers: int | None = None,
    configured_inner_threads: int | None = None,
    configured_target_threads: int | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    stage_key, stage_defaults = _resolve_stage_defaults(stage)
    units = max(1, int(parallel_units or 1))
    normalized_mode = _resolve_execution_mode(execution_mode)
    requested_granularity = str(granularity or stage_defaults.get("granularity") or "candidate")

    if is_unbounded_cpu_execution_mode(normalized_mode):
        return _build_unbounded_policy(
            stage_key=stage_key,
            parallel_units=units,
            granularity=requested_granularity,
            allow_parallel=allow_parallel,
            configured_target_threads=configured_target_threads,
        )

    total_threads = get_total_logical_threads()
    target_requested_raw = _coerce_positive_int(
        configured_target_threads,
        CPU_PARALLEL_TARGET_THREADS,
    )
    target_threads = min(total_threads, target_requested_raw)
    fallback_reasons: list[str] = []

    if target_threads < target_requested_raw:
        fallback_reasons.append(
            f"host_limit:{total_threads}_threads_below_target:{target_requested_raw}"
        )

    nested_caps_applied = should_apply_nested_thread_caps(normalized_mode)
    if nested_outer_parallel and nested_caps_applied:
        fallback_reasons.append("running_inside_outer_parallel_worker")
        nested_limit = _coerce_positive_int(nested_thread_count, target_threads)
        if nested_limit < target_threads:
            fallback_reasons.append(f"nested_worker_budget_limit:{nested_limit}_threads")
        target_threads = min(target_threads, nested_limit)

    preferred_inner_threads = _coerce_positive_int(
        configured_inner_threads,
        int(stage_defaults.get("preferred_inner_threads") or 1),
    )
    preferred_inner_threads = min(preferred_inner_threads, target_threads)
    min_inner_threads = min(
        target_threads,
        _coerce_positive_int(stage_defaults.get("min_inner_threads"), 1),
    )
    requested_outer_workers = None
    if configured_outer_workers is not None:
        requested_outer_workers = _coerce_positive_int(configured_outer_workers, 1)

    outer_parallel_allowed = bool(stage_defaults.get("outer_parallel_allowed", True))
    nested_parallel_allowed = bool(
        CPU_PARALLEL_ENABLE_NESTED_PARALLEL
        and stage_defaults.get("allow_nested_parallel", True)
        and not nested_outer_parallel
    )
    parallel_requested = bool(allow_parallel)

    if not parallel_requested:
        fallback_reasons.append("parallel_disabled_for_stage")
    if not outer_parallel_allowed:
        fallback_reasons.append("stage_prefers_single_outer_worker")
    if units <= 1:
        fallback_reasons.append("single_parallel_unit")

    max_outer_workers = min(units, target_threads)
    if requested_outer_workers is not None:
        max_outer_workers = min(max_outer_workers, requested_outer_workers)

    parallel_enabled = bool(
        parallel_requested
        and outer_parallel_allowed
        and nested_parallel_allowed
        and units > 1
        and target_threads > 1
        and max_outer_workers > 1
    )

    if parallel_enabled:
        outer_workers, inner_threads = _select_parallel_layout(
            parallel_units=units,
            target_threads=target_threads,
            preferred_inner_threads=preferred_inner_threads,
            min_inner_threads=min_inner_threads,
            max_outer_workers=max_outer_workers,
            configured_inner_threads=_coerce_positive_int(configured_inner_threads, 1)
            if configured_inner_threads is not None
            else None,
        )
        parallel_enabled = outer_workers > 1
    else:
        outer_workers = 1
        inner_threads = target_threads

    if not parallel_enabled:
        outer_workers = 1
        inner_threads = target_threads

    nested_parallel = bool(parallel_enabled and inner_threads > 1)
    expected_cpu_budget = outer_workers * inner_threads if parallel_enabled else inner_threads
    if expected_cpu_budget < target_threads:
        fallback_reasons.append(
            f"cpu_budget_shortfall:{expected_cpu_budget}_of_{target_threads}"
        )

    fallback_reason = "none" if expected_cpu_budget >= target_threads else ";".join(dict.fromkeys(fallback_reasons))
    executor_kind = str(stage_defaults.get("preferred_executor") or "thread") if parallel_enabled else "sequential"

    return {
        "stage": stage_key,
        "mode": f"{executor_kind}_parallel_cpu" if parallel_enabled else "sequential_cpu",
        "execution_mode": normalized_mode,
        "parallel_enabled": parallel_enabled,
        "granularity": requested_granularity,
        "nested_parallel": nested_parallel,
        "nested_enabled": nested_parallel,
        "executor_kind": executor_kind,
        "target_threads_requested": target_requested_raw,
        "target_threads": target_threads,
        "total_threads": total_threads,
        "host_threads": total_threads,
        "outer_workers": outer_workers,
        "inner_threads": inner_threads,
        "parallel_units": units,
        "expected_cpu_budget": expected_cpu_budget,
        "estimated_cpu_budget": expected_cpu_budget,
        "catboost_thread_count": inner_threads,
        "worker_thread_limits_applied": True,
        "nested_thread_caps_applied": nested_caps_applied,
        "fallback_reasons": list(dict.fromkeys(fallback_reasons)),
        "fallback_reason": fallback_reason or "none",
        "full_target_reached": expected_cpu_budget >= target_threads,
    }


def format_cpu_stage_policy_log(policy: dict[str, Any]) -> str:
    return (
        f"stage={policy.get('stage')} "
        f"execution_mode={policy.get('execution_mode')} "
        f"worker_thread_limits_applied={policy.get('worker_thread_limits_applied')} "
        f"nested_thread_caps_applied={policy.get('nested_thread_caps_applied')} "
        f"total_threads={policy.get('total_threads')} "
        f"target_threads={policy.get('target_threads')} "
        f"outer_workers={policy.get('outer_workers')} "
        f"inner_threads={policy.get('inner_threads')} "
        f"nested_parallel={policy.get('nested_parallel')} "
        f"parallel_units={policy.get('parallel_units')} "
        f"expected_cpu_budget={policy.get('expected_cpu_budget')} "
        f"executor={policy.get('executor_kind')} "
        f"fallback_reason={policy.get('fallback_reason') or 'none'}"
    )


def resolve_parallel_cpu_settings(
    total_tasks: int,
    configured_workers: int,
    execution_mode: str | None = None,
) -> tuple[int, int | None]:
    del configured_workers
    policy = resolve_cpu_stage_parallel_policy(
        "multi_model_evaluation",
        parallel_units=max(1, int(total_tasks or 1)),
        allow_parallel=ENABLE_PARALLEL_CPU_MULTI_MODEL,
        execution_mode=execution_mode,
    )
    return int(policy["outer_workers"]), policy.get("catboost_thread_count")


def resolve_stage2_parallel_policy(
    candidate_count: int,
    fold_count: int,
    *,
    nested_outer_parallel: bool = False,
    requested_mode: str | None = None,
    requested_granularity: str | None = None,
    allow_parallel: bool = ENABLE_PARALLEL_CPU_FULL_EVALUATION,
    configured_target_threads: int | None = None,
    execution_mode: str | None = None,
) -> dict[str, Any]:
    candidates = max(1, int(candidate_count or 1))
    folds = max(1, int(fold_count or 1))
    normalized_mode = _resolve_execution_mode(execution_mode)

    mode = str(requested_mode or STAGE2_PARALLEL_MODE or "adaptive_cpu").lower()
    granularity = str(requested_granularity or STAGE2_PARALLEL_GRANULARITY or "candidate_fold").lower()

    if mode not in {"adaptive_cpu", "candidate_cpu", "candidate_fold_cpu", "sequential_cpu"}:
        mode = "adaptive_cpu"
    if granularity not in {"adaptive", "candidate", "model", "candidate_fold", "fold"}:
        granularity = "candidate_fold"

    if mode == "candidate_cpu":
        granularity = "candidate"
    elif mode == "candidate_fold_cpu":
        granularity = "candidate_fold"
    elif granularity == "adaptive":
        granularity = "candidate_fold" if folds > 1 else "candidate"
    elif granularity in {"model", "candidate"}:
        granularity = "candidate"
    elif granularity == "fold":
        granularity = "candidate_fold"

    nested_caps_applied = should_apply_nested_thread_caps(normalized_mode)
    parallel_units = candidates * folds if granularity == "candidate_fold" else candidates
    policy = resolve_cpu_stage_parallel_policy(
        "stage2_full_evaluation",
        parallel_units=parallel_units,
        granularity=granularity,
        nested_outer_parallel=nested_outer_parallel if nested_caps_applied else False,
        nested_thread_count=current_worker_thread_count(normalized_mode) if nested_outer_parallel and nested_caps_applied else None,
        allow_parallel=allow_parallel and mode != "sequential_cpu",
        configured_target_threads=configured_target_threads,
        execution_mode=normalized_mode,
    )
    policy["mode"] = mode if policy["parallel_enabled"] else policy["mode"]
    policy["candidate_count"] = candidates
    policy["fold_count"] = folds
    return policy


def apply_cpu_worker_limits(
    thread_count: int | None,
    *,
    mark_outer_parallel: bool = False,
    execution_mode: str | None = None,
) -> int | None:
    normalized_mode = activate_cpu_execution_mode(
        execution_mode,
        mark_outer_parallel=mark_outer_parallel,
    )
    if not should_apply_worker_thread_limits(normalized_mode):
        return None

    safe_threads = _coerce_positive_int(thread_count, CPU_PARALLEL_TARGET_THREADS)
    thread_str = str(safe_threads)
    for env_var in CPU_WORKER_THREAD_ENV_VARS:
        os.environ[env_var] = thread_str
    os.environ["CATBOOST_WORKER_THREADS"] = thread_str
    os.environ["CATBOOST_OUTER_PARALLEL"] = "1" if mark_outer_parallel else "0"
    return safe_threads


def current_worker_thread_count(
    execution_mode: str | None = None,
) -> int | None:
    if not should_apply_nested_thread_caps(execution_mode):
        return None
    raw = os.environ.get("CATBOOST_WORKER_THREADS")
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def is_nested_outer_parallel(
    execution_mode: str | None = None,
) -> bool:
    if not should_apply_nested_thread_caps(execution_mode):
        return False
    return os.environ.get("CATBOOST_OUTER_PARALLEL", "0") == "1"


_COMPAT_STAGE2_POLICY = resolve_cpu_stage_parallel_policy(
    "stage2_full_evaluation",
    parallel_units=max(2, CPU_LOGICAL_THREADS),
    allow_parallel=ENABLE_PARALLEL_CPU_FULL_EVALUATION,
    execution_mode=CPU_EXECUTION_MODE_ADAPTIVE,
)
_COMPAT_MULTI_MODEL_POLICY = resolve_cpu_stage_parallel_policy(
    "multi_model_evaluation",
    parallel_units=max(2, CPU_LOGICAL_THREADS),
    allow_parallel=ENABLE_PARALLEL_CPU_MULTI_MODEL,
    execution_mode=CPU_EXECUTION_MODE_ADAPTIVE,
)

PARALLEL_EVAL_WORKERS = int(_COMPAT_STAGE2_POLICY["outer_workers"])
PARALLEL_BACKTEST_WORKERS = 1
PARALLEL_MULTI_MODEL_WORKERS = int(_COMPAT_MULTI_MODEL_POLICY["outer_workers"])
CATBOOST_THREADS_PER_WORKER = int(_COMPAT_STAGE2_POLICY["inner_threads"])
