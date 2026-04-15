from __future__ import annotations

from typing import Any

from catboost_floader.diagnostics.artifact_readers import (
    load_model_backtest_summary,
    load_model_comparison_vs_baselines,
    load_model_multi_window_summary,
    load_model_pipeline_metadata,
    load_pipeline_summary,
)
from catboost_floader.diagnostics.artifact_registry import MAIN_MODEL_KEY, list_model_keys, model_artifact_paths


def _safe_float(value: Any) -> float | None:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if value_f != value_f:
        return None
    return value_f


def _safe_int(value: Any) -> int | None:
    try:
        value_i = int(value)
    except (TypeError, ValueError):
        return None
    return value_i


def _accuracy_pct(value: Any) -> float | None:
    value_f = _safe_float(value)
    if value_f is None:
        return None
    return round(value_f * 100.0, 2)


def _lookup_first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _lookup_dict(key: str, *sources: Any) -> dict[str, Any]:
    for source in sources:
        if isinstance(source, dict):
            value = source.get(key)
            if isinstance(value, dict):
                return dict(value)
    return {}


def _lookup_value(key: str, *sources: Any) -> Any:
    for source in sources:
        if isinstance(source, dict) and source.get(key) is not None:
            return source.get(key)
    return None


def _display_model_name(model_key: str) -> str:
    if model_key == MAIN_MODEL_KEY:
        return "Main Pipeline"
    return str(model_key)


def _is_robust_status(status: Any) -> bool:
    return str(status or "").lower().startswith("robust")


def _derive_recommendation_bucket(row: dict[str, Any]) -> str:
    explicit = row.get("recommendation_bucket")
    if explicit:
        return str(explicit)

    eligible = bool(row.get("selection_eligibility", False))
    robust = _is_robust_status(row.get("robustness_status"))
    overfit_status = str(row.get("overfit_status") or "none").lower()
    guarded_delta = _safe_float(row.get("delta_vs_baseline"))
    raw_delta = _safe_float(row.get("raw_model_delta_vs_baseline"))

    if eligible and robust and guarded_delta is not None and guarded_delta > 0:
        return "Preferred"
    if raw_delta is not None and raw_delta > 0 and (guarded_delta is None or guarded_delta <= 0):
        return "Suppressed Edge"
    if eligible and guarded_delta is not None and guarded_delta > 0:
        return "Usable"
    if overfit_status in {"moderate", "severe"}:
        return "Overfit Risk"
    if not eligible:
        return "Suppressed"
    return "Watch"


def _extract_guarded_delta(metric_summary: dict[str, Any]) -> float | None:
    direct_model = dict(metric_summary.get("direct_model", {}) or {})
    baselines = dict(metric_summary.get("direct_baselines", {}) or {})
    persistence = dict(baselines.get("persistence", {}) or {})
    direct_mae = _safe_float(direct_model.get("MAE"))
    baseline_mae = _safe_float(persistence.get("MAE"))
    if direct_mae is None or baseline_mae is None:
        return _safe_float(metric_summary.get("final_holdout_delta_vs_baseline"))
    return float(baseline_mae - direct_mae)


def _extract_robustness_metrics(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
    multi_window_summary: dict[str, Any],
) -> dict[str, Any]:
    direct_strategy = _lookup_dict("direct_strategy", metric_summary, pipeline_metadata, summary_seed)
    multi_window_seed = _lookup_dict("multi_window", summary_seed, pipeline_metadata)
    aggregate_metrics = _lookup_dict("aggregate_metrics", multi_window_summary, multi_window_seed)
    robustness = dict(direct_strategy.get("robustness_metrics", {}) or {})
    if not robustness:
        robustness = aggregate_metrics
    return {
        "robustness_status": _lookup_value("robustness_status", summary_seed, metric_summary, pipeline_metadata),
        "selection_eligibility": bool(
            _lookup_first(
                _lookup_value("selection_eligibility", summary_seed, metric_summary, pipeline_metadata),
                True,
            )
        ),
        "disabled_by_robustness": bool(
            _lookup_first(
                _lookup_value("disabled_by_robustness", summary_seed, metric_summary, pipeline_metadata),
                False,
            )
        ),
        "robustness_disable_reason": _lookup_value(
            "robustness_disable_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "mean_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("mean_delta_vs_baseline"),
                aggregate_metrics.get("mean_delta_vs_baseline"),
            )
        ),
        "std_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("std_delta_vs_baseline"),
                aggregate_metrics.get("std_delta_vs_baseline"),
            )
        ),
        "win_rate_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("win_rate_vs_baseline"),
                robustness.get("model_win_rate_vs_baseline"),
                aggregate_metrics.get("win_rate_vs_baseline"),
                aggregate_metrics.get("model_win_rate_vs_baseline"),
            )
        ),
        "mean_sign_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("mean_sign_accuracy_pct"),
                aggregate_metrics.get("mean_sign_accuracy_pct"),
            )
        ),
        "std_sign_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("std_sign_accuracy_pct"),
                aggregate_metrics.get("std_sign_accuracy_pct"),
            )
        ),
        "mean_direction_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("mean_direction_accuracy_pct"),
                aggregate_metrics.get("mean_direction_accuracy_pct"),
            )
        ),
        "best_window_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("best_window_delta_vs_baseline"),
                aggregate_metrics.get("best_window_delta_vs_baseline"),
            )
        ),
        "worst_window_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("worst_window_delta_vs_baseline"),
                aggregate_metrics.get("worst_window_delta_vs_baseline"),
            )
        ),
    }


def _extract_overfitting_fields(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = _lookup_dict("overfitting_diagnostics", summary_seed, metric_summary, pipeline_metadata)
    fields = [
        "train_MAE",
        "val_MAE",
        "holdout_MAE",
        "train_sign_acc",
        "train_sign_acc_pct",
        "val_sign_acc",
        "val_sign_acc_pct",
        "holdout_sign_acc",
        "holdout_sign_acc_pct",
        "mae_gap_train_val",
        "mae_gap_train_holdout",
        "sign_gap_train_val",
        "sign_gap_train_holdout",
        "mae_overfit_ratio",
        "holdout_overfit_ratio",
        "overfit_status",
        "overfit_reason",
        "train_delta_vs_baseline",
        "val_delta_vs_baseline",
        "holdout_delta_vs_baseline",
    ]
    payload: dict[str, Any] = {"diagnostics": diagnostics}
    for field in fields:
        payload[field] = _lookup_first(
            diagnostics.get(field),
            _lookup_value(field, summary_seed, metric_summary, pipeline_metadata),
        )
    for acc_field, pct_field in [
        ("train_sign_acc", "train_sign_acc_pct"),
        ("val_sign_acc", "val_sign_acc_pct"),
        ("holdout_sign_acc", "holdout_sign_acc_pct"),
    ]:
        if payload.get(pct_field) is None:
            payload[pct_field] = _accuracy_pct(payload.get(acc_field))
    return payload


def _extract_sign_confusion_fields(
    metric_summary: dict[str, Any],
    direct_model: dict[str, Any],
    accuracy: dict[str, Any],
) -> dict[str, int | None]:
    sign_confusion = _lookup_dict("sign_confusion", direct_model, accuracy, metric_summary)
    return {
        "sign_tp": _safe_int(_lookup_first(sign_confusion.get("true_positive"), _lookup_value("sign_tp", direct_model, accuracy, metric_summary))),
        "sign_tn": _safe_int(_lookup_first(sign_confusion.get("true_negative"), _lookup_value("sign_tn", direct_model, accuracy, metric_summary))),
        "sign_fp": _safe_int(_lookup_first(sign_confusion.get("false_positive"), _lookup_value("sign_fp", direct_model, accuracy, metric_summary))),
        "sign_fn": _safe_int(_lookup_first(sign_confusion.get("false_negative"), _lookup_value("sign_fn", direct_model, accuracy, metric_summary))),
    }


def _extract_raw_model_metrics(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    metrics = _lookup_dict("raw_model_metrics", summary_seed, metric_summary, pipeline_metadata)
    raw_keys = [
        "raw_model_MAE",
        "raw_model_sign_acc",
        "raw_model_sign_acc_pct",
        "raw_model_direction_acc",
        "raw_model_direction_acc_pct",
        "raw_model_delta_vs_baseline",
        "raw_model_mean_delta_vs_baseline",
        "raw_model_std_delta_vs_baseline",
        "raw_model_win_rate_vs_baseline",
    ]
    for key in raw_keys:
        if key not in metrics:
            metrics[key] = _lookup_value(key, summary_seed, metric_summary, pipeline_metadata)
    return metrics


def _extract_selection_fields(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    direct_strategy = _lookup_dict("direct_strategy", metric_summary, pipeline_metadata, summary_seed)
    selection = {
        "selected_candidate_type": str(_lookup_first(direct_strategy.get("type"), "model_only")),
        "main_selection_candidate_type": _lookup_value(
            "main_selection_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "main_selection_final_ranking_reason": _lookup_value(
            "main_selection_final_ranking_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "main_selection_relaxed_rule_applied": bool(
            _lookup_first(
                _lookup_value(
                    "main_selection_relaxed_rule_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "main_selection_baseline_overridden": bool(
            _lookup_first(
                _lookup_value(
                    "main_selection_baseline_overridden",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "main_persistence_promotion_applied": bool(
            _lookup_first(
                _lookup_value(
                    "main_persistence_promotion_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "final_holdout_guard_reason": _lookup_value(
            "final_holdout_guard_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "final_holdout_guard_applied": bool(
            _lookup_first(
                _lookup_value(
                    "final_holdout_guard_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "raw_model_candidate_type": _lookup_value(
            "raw_model_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "raw_model_used_before_guard": bool(
            _lookup_first(
                _lookup_value(
                    "raw_model_used_before_guard",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                True,
            )
        ),
        "guarded_candidate_type": _lookup_value(
            "guarded_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "guarded_candidate_after_guard": bool(
            _lookup_first(
                _lookup_value(
                    "guarded_candidate_after_guard",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "final_holdout_candidate_before_guard": _lookup_dict(
            "final_holdout_candidate_before_guard",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "final_holdout_candidate_after_guard": _lookup_dict(
            "final_holdout_candidate_after_guard",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "validation_mae": _safe_float(direct_strategy.get("validation_mae")),
        "selection_pool": direct_strategy.get("selection_pool"),
        "composition_profile": direct_strategy.get("composition_profile"),
        "profile_selection_mode": direct_strategy.get("profile_selection_mode"),
        "profile_evaluations": list(direct_strategy.get("profile_evaluations", []) or []),
        "main_selection_relaxed_rule": dict(direct_strategy.get("main_selection_relaxed_rule", {}) or {}),
        "main_persistence_promotion": dict(direct_strategy.get("main_persistence_promotion", {}) or {}),
        "direct_strategy": direct_strategy,
    }
    if selection["main_selection_candidate_type"] is None:
        selection["main_selection_candidate_type"] = selection["selected_candidate_type"]
    if selection["raw_model_candidate_type"] is None:
        selection["raw_model_candidate_type"] = "model_only"
    if selection["guarded_candidate_type"] is None:
        selection["guarded_candidate_type"] = selection["main_selection_candidate_type"]
    return selection


def build_model_snapshot(
    model_key: str,
    *,
    summary_seed: dict[str, Any] | None = None,
    pipeline_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary_seed = dict(summary_seed or {})
    backtest_summary = dict(load_model_backtest_summary(model_key) or {})
    pipeline_metadata = dict(load_model_pipeline_metadata(model_key) or {})
    multi_window_summary = dict(load_model_multi_window_summary(model_key) or {})
    comparison_vs_baselines = dict(load_model_comparison_vs_baselines(model_key) or {})
    metric_summary = dict(backtest_summary or summary_seed.get("metrics", {}) or summary_seed.get("backtest_summary", {}) or {})

    direct_model = dict(metric_summary.get("direct_model", {}) or {})
    accuracy = dict(metric_summary.get("accuracy_metrics", {}) or {})
    robustness = _extract_robustness_metrics(summary_seed, metric_summary, pipeline_metadata, multi_window_summary)
    raw_model_metrics = _extract_raw_model_metrics(summary_seed, metric_summary, pipeline_metadata)
    selection = _extract_selection_fields(summary_seed, metric_summary, pipeline_metadata)
    overfitting = _extract_overfitting_fields(summary_seed, metric_summary, pipeline_metadata)

    sign_acc = _safe_float(_lookup_first(direct_model.get("sign_accuracy"), accuracy.get("sign_accuracy")))
    sign_acc_pct = _safe_float(
        _lookup_first(
            metric_summary.get("sign_accuracy_pct"),
            direct_model.get("sign_accuracy_pct"),
            accuracy.get("sign_accuracy_pct"),
        )
    )
    sign_confusion = _extract_sign_confusion_fields(metric_summary, direct_model, accuracy)
    direction_acc = _safe_float(_lookup_first(accuracy.get("direction_accuracy"), metric_summary.get("direction_accuracy")))
    direction_acc_pct = _safe_float(
        _lookup_first(
            metric_summary.get("direction_accuracy_pct"),
            accuracy.get("direction_accuracy_pct"),
        )
    )
    summary = {
        "MAE": _safe_float(direct_model.get("MAE")),
        "RMSE": _safe_float(direct_model.get("RMSE")),
        "MAPE": _safe_float(direct_model.get("MAPE")),
        "return_MAE": _safe_float(direct_model.get("return_MAE")),
        "sign_acc": sign_acc,
        "sign_acc_pct": sign_acc_pct,
        **sign_confusion,
        "direction_acc": direction_acc,
        "direction_acc_pct": direction_acc_pct,
        "delta_vs_baseline": _extract_guarded_delta(metric_summary),
        "robustness_status": robustness.get("robustness_status"),
        "selection_eligibility": robustness.get("selection_eligibility"),
        "overfit_status": overfitting.get("overfit_status"),
        "overfit_reason": overfitting.get("overfit_reason"),
    }

    registry_row = {
        "model_key": model_key,
        "model_name": _display_model_name(model_key),
        "is_main": model_key == MAIN_MODEL_KEY,
        "robustness_status": summary["robustness_status"],
        "selection_eligibility": summary["selection_eligibility"],
        "delta_vs_baseline": summary["delta_vs_baseline"],
        "mean_delta_vs_baseline": robustness.get("mean_delta_vs_baseline"),
        "std_delta_vs_baseline": robustness.get("std_delta_vs_baseline"),
        "win_rate_vs_baseline": robustness.get("win_rate_vs_baseline"),
        "sign_acc_pct": summary["sign_acc_pct"],
        "sign_tp": summary.get("sign_tp"),
        "sign_tn": summary.get("sign_tn"),
        "sign_fp": summary.get("sign_fp"),
        "sign_fn": summary.get("sign_fn"),
        "direction_acc_pct": summary["direction_acc_pct"],
        "overfit_status": summary["overfit_status"],
        "overfit_reason": summary["overfit_reason"],
        "raw_model_delta_vs_baseline": raw_model_metrics.get("raw_model_delta_vs_baseline"),
        "raw_model_sign_acc_pct": raw_model_metrics.get("raw_model_sign_acc_pct"),
        "raw_model_direction_acc_pct": raw_model_metrics.get("raw_model_direction_acc_pct"),
        "raw_model_candidate_type": selection.get("raw_model_candidate_type"),
        "raw_model_used_before_guard": selection.get("raw_model_used_before_guard"),
        "guarded_candidate_type": selection.get("guarded_candidate_type"),
        "guarded_candidate_after_guard": selection.get("guarded_candidate_after_guard"),
        "recommendation_bucket": _lookup_value("recommendation_bucket", summary_seed, metric_summary, pipeline_metadata),
    }
    registry_row["recommendation_bucket"] = _derive_recommendation_bucket(registry_row)
    artifact_paths = model_artifact_paths(model_key)

    return {
        "model_key": model_key,
        "model_name": _display_model_name(model_key),
        "is_main": model_key == MAIN_MODEL_KEY,
        "summary": summary,
        "raw_model": raw_model_metrics,
        "overfitting": overfitting,
        "robustness": robustness,
        "selection": selection,
        "registry": registry_row,
        "artifact_paths": artifact_paths,
        "artifacts": {
            "pipeline_summary_entry": summary_seed,
            "pipeline_summary": dict(pipeline_summary or {}),
            "backtest_summary": backtest_summary,
            "pipeline_metadata": pipeline_metadata,
            "multi_window_summary": multi_window_summary,
            "comparison_vs_baselines": comparison_vs_baselines,
        },
    }


def build_model_snapshots(pipeline_summary: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    pipeline_summary = dict(pipeline_summary or load_pipeline_summary() or {})
    pipeline_multi = dict(pipeline_summary.get("multi_models", {}) or {})
    records: dict[str, dict[str, Any]] = {}
    for key in list_model_keys(pipeline_summary):
        summary_seed = dict(pipeline_summary if key == MAIN_MODEL_KEY else pipeline_multi.get(key, {}) or {})
        records[key] = build_model_snapshot(
            key,
            summary_seed=summary_seed,
            pipeline_summary=pipeline_summary,
        )
    return records


def build_model_registry_rows(pipeline_summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rows = [
        dict(snapshot.get("registry", {}) or {})
        for snapshot in build_model_snapshots(pipeline_summary).values()
        if snapshot.get("registry")
    ]
    return sorted(
        rows,
        key=lambda row: (
            not bool(row.get("selection_eligibility", False)),
            -float(_safe_float(row.get("delta_vs_baseline")) or float("-inf")),
            -float(_safe_float(row.get("mean_delta_vs_baseline")) or float("-inf")),
        ),
    )