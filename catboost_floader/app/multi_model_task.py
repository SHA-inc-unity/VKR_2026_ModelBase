from __future__ import annotations

from typing import Any, Dict

from catboost_floader.core.config import (
    ENABLE_MULTI_WINDOW_EVALUATION,
    EVALUATION_WINDOW_COUNT,
    EVALUATION_WINDOW_SIZE,
    EVALUATION_WINDOW_STEP,
    apply_cpu_worker_limits,
)
from catboost_floader.core.utils import get_logger
from catboost_floader.diagnostics.artifact_registry import _multi_model_artifact_paths
from catboost_floader.diagnostics.overfitting_diagnostics import overfitting_flat_fields
from catboost_floader.targets.generation import generate_direct_targets, generate_range_targets

from catboost_floader.app.pipeline_execution import _run_pipeline_bundle
from catboost_floader.app.pipeline_preparation import _prepare_pipeline_splits, _tune_pipeline_models
from catboost_floader.selection.composition_profiles import _direct_composition_profile_for_key

logger = get_logger("multi_model_task")


def _run_multi_model_key_task(task: Dict[str, Any]) -> Dict[str, Any]:
    key = task["key"]
    thread_count = task.get("catboost_thread_count")
    outer_parallel_worker = bool(task.get("outer_parallel_worker", False))
    if thread_count is not None:
        apply_cpu_worker_limits(thread_count, mark_outer_parallel=outer_parallel_worker)

    logger.info(
        "CPU-parallel multi-model worker started for %s with catboost_thread_count=%s",
        key,
        thread_count,
    )

    direct_targets_tf = generate_direct_targets(task["df_tf"], horizon_steps=task["steps"])
    range_targets_tf = generate_range_targets(task["df_tf"], future_window=task["steps"])

    prepared_tf = _prepare_pipeline_splits(
        task["direct_features"],
        task["range_features"],
        direct_targets_tf,
        range_targets_tf,
    )
    if (
        prepared_tf["X_direct_fit_model"].empty
        or prepared_tf["X_direct_test_model"].empty
        or prepared_tf["X_range_fit_model"].empty
        or prepared_tf["X_range_test_model"].empty
    ):
        return {
            "status": "skipped",
            "key": key,
            "message": "empty aligned splits after anomaly-aware preparation",
        }

    tuning_tf = _tune_pipeline_models(prepared_tf, skip_tuning=task["skip_tuning"])
    direct_composition_profile = _direct_composition_profile_for_key(key)
    artifacts = _multi_model_artifact_paths(key)
    result_tf = _run_pipeline_bundle(
        prepared_tf,
        direct_params=tuning_tf["direct"],
        range_low_params=tuning_tf["range_low"],
        range_high_params=tuning_tf["range_high"],
        direct_composition_profile=direct_composition_profile,
        catboost_thread_count=thread_count,
        model_key=key,
        enable_multi_window_evaluation=ENABLE_MULTI_WINDOW_EVALUATION,
        evaluation_window_count=EVALUATION_WINDOW_COUNT,
        evaluation_window_size=EVALUATION_WINDOW_SIZE,
        evaluation_window_step=EVALUATION_WINDOW_STEP,
        model_dir=artifacts["model_dir"],
        report_dir=artifacts["report_dir"],
        backtest_dir=artifacts["backtest_dir"],
    )
    overfitting_diagnostics = dict(result_tf.get("overfitting_diagnostics", {}) or {})
    overfitting_metrics = overfitting_flat_fields(result_tf)
    raw_model_metrics = dict(
        result_tf.get(
            "raw_model_metrics",
            dict(result_tf.get("backtest_summary", {}) or {}).get("raw_model_metrics", {}),
        )
        or {}
    )

    summary = {
        "rows": int(len(prepared_tf["X_direct_test_model"])),
        "direct_composition_profile": result_tf["direct_composition_profile"],
        "direct_composition_config": result_tf["direct_composition_config"],
        "direct_strategy": result_tf["direct_strategy"],
        "direct_strategy_robustness": result_tf.get("direct_strategy_robustness", {}),
        "robustness_classification": result_tf.get("robustness_classification", {}),
        "robustness_status": dict(result_tf.get("robustness_classification", {}) or {}).get("robustness_status"),
        "disabled_by_robustness": bool(dict(result_tf.get("robustness_classification", {}) or {}).get("disabled_by_robustness", False)),
        "robustness_disable_reason": dict(result_tf.get("robustness_classification", {}) or {}).get("robustness_disable_reason"),
        "selection_eligibility": bool(dict(result_tf.get("robustness_classification", {}) or {}).get("selection_eligibility", True)),
        "final_holdout_safeguard_applied": bool(dict(result_tf.get("robustness_classification", {}) or {}).get("final_holdout_safeguard_applied", False)),
        "overfitting_diagnostics": overfitting_diagnostics,
        **overfitting_metrics,
        "raw_model_metrics": raw_model_metrics,
        **raw_model_metrics,
        "raw_model_candidate_type": result_tf.get("raw_model_candidate_type"),
        "raw_model_used_before_guard": result_tf.get("raw_model_used_before_guard"),
        "guarded_candidate_type": result_tf.get("guarded_candidate_type"),
        "guarded_candidate_after_guard": result_tf.get("guarded_candidate_after_guard"),
        "range_calibration": result_tf["range_calibration"],
        "metrics": result_tf["backtest_summary"],
        "backtest_points": result_tf["backtest_summary"].get("backtest_points"),
        "direction_points": result_tf["backtest_summary"].get("direction_points"),
        "accuracy_metrics": result_tf.get("accuracy_metrics", {}),
        "direction_accuracy_pct": result_tf.get("accuracy_metrics", {}).get("direction_accuracy_pct"),
        "sign_accuracy_pct": result_tf.get("accuracy_metrics", {}).get("sign_accuracy_pct"),
        "multi_window": result_tf.get("multi_window", {}),
        "artifacts": artifacts,
    }
    logger.info("CPU-parallel multi-model worker finished for %s", key)
    return {"status": "ok", "key": key, "summary": summary}
