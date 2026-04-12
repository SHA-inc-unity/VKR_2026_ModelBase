from __future__ import annotations

import os
from typing import Any, Dict

from catboost_floader.core.config import (
    ENABLE_MULTI_WINDOW_EVALUATION,
    MULTI_WINDOW_RANKING_METRIC,
    REPORT_DIR,
)
from catboost_floader.diagnostics.overfitting_diagnostics import overfitting_flat_fields
from catboost_floader.evaluation.multi_window import save_global_multi_window_ranking


def _build_pipeline_summary(
    *,
    prepared_main: Dict[str, Any],
    main_result: Dict[str, Any],
    multi_models_summary: Dict[str, Dict[str, Any]],
    live_result: Dict[str, Any],
) -> Dict[str, Any]:
    raw_metric_keys = [
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

    practical_selection_registry: dict[str, Dict[str, Any]] = {}
    main_classification = dict(main_result.get("robustness_classification", {}) or {})
    main_backtest_summary = dict(main_result.get("backtest_summary", {}) or {})
    main_overfitting_diagnostics = dict(
        main_result.get("overfitting_diagnostics", main_backtest_summary.get("overfitting_diagnostics", {})) or {}
    )
    main_overfitting_metrics = overfitting_flat_fields(main_result)
    if not any(value is not None for value in main_overfitting_metrics.values()):
        main_overfitting_metrics = overfitting_flat_fields(main_backtest_summary)
    main_final_holdout_guard_applied = bool(
        main_result.get(
            "final_holdout_guard_applied",
            main_backtest_summary.get("final_holdout_guard_applied", False),
        )
    )
    main_final_holdout_guard_reason = main_result.get(
        "final_holdout_guard_reason",
        main_backtest_summary.get("final_holdout_guard_reason"),
    )
    main_final_holdout_delta_vs_baseline = main_result.get(
        "final_holdout_delta_vs_baseline",
        main_backtest_summary.get("final_holdout_delta_vs_baseline"),
    )
    main_final_holdout_candidate_before_guard = main_result.get(
        "final_holdout_candidate_before_guard",
        main_backtest_summary.get("final_holdout_candidate_before_guard", {}),
    )
    main_final_holdout_candidate_after_guard = main_result.get(
        "final_holdout_candidate_after_guard",
        main_backtest_summary.get("final_holdout_candidate_after_guard", {}),
    )
    main_selection_relaxed_rule_applied = bool(
        main_result.get(
            "main_selection_relaxed_rule_applied",
            main_backtest_summary.get("main_selection_relaxed_rule_applied", False),
        )
    )
    main_selection_final_ranking_reason = str(
        main_result.get(
            "main_selection_final_ranking_reason",
            main_backtest_summary.get("main_selection_final_ranking_reason", ""),
        )
    )
    main_selection_baseline_overridden = bool(
        main_result.get(
            "main_selection_baseline_overridden",
            main_backtest_summary.get("main_selection_baseline_overridden", False),
        )
    )
    main_selection_candidate_type = str(
        main_result.get(
            "main_selection_candidate_type",
            main_backtest_summary.get("main_selection_candidate_type", "model_only"),
        )
    )
    main_persistence_promotion_applied = bool(
        main_result.get(
            "main_persistence_promotion_applied",
            main_backtest_summary.get("main_persistence_promotion_applied", False),
        )
    )
    main_persistence_promotable_candidate_count = int(
        main_result.get(
            "main_persistence_promotable_candidate_count",
            main_backtest_summary.get("main_persistence_promotable_candidate_count", 0),
        )
        or 0
    )
    main_persistence_promotable_non_baseline_count = int(
        main_result.get(
            "main_persistence_promotable_non_baseline_count",
            main_backtest_summary.get("main_persistence_promotable_non_baseline_count", 0),
        )
        or 0
    )
    main_persistence_baseline_excluded_from_promotion = bool(
        main_result.get(
            "main_persistence_baseline_excluded_from_promotion",
            main_backtest_summary.get("main_persistence_baseline_excluded_from_promotion", False),
        )
    )
    main_raw_model_metrics = dict(
        main_result.get(
            "raw_model_metrics",
            main_backtest_summary.get("raw_model_metrics", {}),
        )
        or {}
    )
    for metric_key in raw_metric_keys:
        if metric_key not in main_raw_model_metrics:
            metric_value = main_result.get(metric_key, main_backtest_summary.get(metric_key))
            if metric_value is not None:
                main_raw_model_metrics[metric_key] = metric_value

    main_raw_model_candidate_type = str(
        main_result.get(
            "raw_model_candidate_type",
            main_backtest_summary.get("raw_model_candidate_type", "model_only"),
        )
    )
    main_raw_model_used_before_guard = bool(
        main_result.get(
            "raw_model_used_before_guard",
            main_backtest_summary.get("raw_model_used_before_guard", True),
        )
    )
    main_guarded_candidate_type = str(
        main_result.get(
            "guarded_candidate_type",
            main_backtest_summary.get("guarded_candidate_type", main_selection_candidate_type),
        )
    )
    main_guarded_candidate_after_guard = bool(
        main_result.get(
            "guarded_candidate_after_guard",
            main_backtest_summary.get("guarded_candidate_after_guard", main_final_holdout_guard_applied),
        )
    )

    practical_selection_registry["main_direct_pipeline"] = {
        "robustness_status": main_classification.get("robustness_status"),
        "disabled_by_robustness": bool(main_classification.get("disabled_by_robustness", False)),
        "robustness_disable_reason": main_classification.get("robustness_disable_reason"),
        "selection_eligibility": bool(main_classification.get("selection_eligibility", True)),
        "final_holdout_safeguard_applied": bool(main_classification.get("final_holdout_safeguard_applied", False)),
        "overfit_status": main_overfitting_metrics.get("overfit_status"),
        "overfit_reason": main_overfitting_metrics.get("overfit_reason"),
        "final_holdout_guard_applied": main_final_holdout_guard_applied,
        "final_holdout_guard_reason": main_final_holdout_guard_reason,
        "final_holdout_delta_vs_baseline": main_final_holdout_delta_vs_baseline,
        "main_selection_relaxed_rule_applied": main_selection_relaxed_rule_applied,
        "main_selection_final_ranking_reason": main_selection_final_ranking_reason,
        "main_selection_baseline_overridden": main_selection_baseline_overridden,
        "main_selection_candidate_type": main_selection_candidate_type,
        "main_persistence_promotion_applied": main_persistence_promotion_applied,
        "main_persistence_promotable_candidate_count": main_persistence_promotable_candidate_count,
        "main_persistence_promotable_non_baseline_count": main_persistence_promotable_non_baseline_count,
        "main_persistence_baseline_excluded_from_promotion": main_persistence_baseline_excluded_from_promotion,
        "raw_model_metrics": main_raw_model_metrics,
        **main_raw_model_metrics,
        "raw_model_candidate_type": main_raw_model_candidate_type,
        "raw_model_used_before_guard": main_raw_model_used_before_guard,
        "guarded_candidate_type": main_guarded_candidate_type,
        "guarded_candidate_after_guard": main_guarded_candidate_after_guard,
    }
    for key, model_summary in multi_models_summary.items():
        model_classification = dict(model_summary.get("robustness_classification", {}) or {})
        model_overfitting_metrics = overfitting_flat_fields(model_summary)
        model_metrics_summary = dict(model_summary.get("metrics", {}) or {})
        model_raw_model_metrics = dict(
            model_summary.get("raw_model_metrics", model_metrics_summary.get("raw_model_metrics", {})) or {}
        )
        for metric_key in raw_metric_keys:
            if metric_key not in model_raw_model_metrics:
                metric_value = model_summary.get(metric_key, model_metrics_summary.get(metric_key))
                if metric_value is not None:
                    model_raw_model_metrics[metric_key] = metric_value

        model_selection_candidate_type = str(
            model_summary.get(
                "main_selection_candidate_type",
                model_metrics_summary.get("main_selection_candidate_type", "model_only"),
            )
        )
        model_final_holdout_guard_applied = bool(
            model_summary.get(
                "final_holdout_guard_applied",
                model_metrics_summary.get("final_holdout_guard_applied", False),
            )
        )
        practical_selection_registry[key] = {
            "robustness_status": model_classification.get("robustness_status"),
            "disabled_by_robustness": bool(model_classification.get("disabled_by_robustness", False)),
            "robustness_disable_reason": model_classification.get("robustness_disable_reason"),
            "selection_eligibility": bool(model_classification.get("selection_eligibility", True)),
            "final_holdout_safeguard_applied": bool(model_classification.get("final_holdout_safeguard_applied", False)),
            "overfit_status": model_overfitting_metrics.get("overfit_status"),
            "overfit_reason": model_overfitting_metrics.get("overfit_reason"),
            "raw_model_metrics": model_raw_model_metrics,
            **model_raw_model_metrics,
            "raw_model_candidate_type": str(
                model_summary.get(
                    "raw_model_candidate_type",
                    model_metrics_summary.get("raw_model_candidate_type", "model_only"),
                )
            ),
            "raw_model_used_before_guard": bool(
                model_summary.get(
                    "raw_model_used_before_guard",
                    model_metrics_summary.get("raw_model_used_before_guard", True),
                )
            ),
            "guarded_candidate_type": str(
                model_summary.get(
                    "guarded_candidate_type",
                    model_metrics_summary.get("guarded_candidate_type", model_selection_candidate_type),
                )
            ),
            "guarded_candidate_after_guard": bool(
                model_summary.get(
                    "guarded_candidate_after_guard",
                    model_metrics_summary.get("guarded_candidate_after_guard", model_final_holdout_guard_applied),
                )
            ),
        }

    practical_ranking_excluded_models = [
        key for key, info in practical_selection_registry.items() if not bool(info.get("selection_eligibility", True))
    ]
    practical_ranking_included_models = [
        key for key, info in practical_selection_registry.items() if bool(info.get("selection_eligibility", True))
    ]

    multi_window_ranking = None
    if ENABLE_MULTI_WINDOW_EVALUATION:
        model_multi_window_summary: dict[str, dict[str, Any]] = {}
        main_multi_window = main_result.get("multi_window", {})
        main_selection_eligible = bool(
            dict(main_result.get("robustness_classification", {}) or {}).get("selection_eligibility", True)
        )
        if isinstance(main_multi_window, dict) and main_multi_window.get("enabled") and main_selection_eligible:
            model_multi_window_summary["main_direct_pipeline"] = main_multi_window
        for key, model_summary in multi_models_summary.items():
            if not isinstance(model_summary, dict):
                continue
            if not bool(dict(model_summary.get("robustness_classification", {}) or {}).get("selection_eligibility", True)):
                continue
            multi_window = model_summary.get("multi_window", {})
            if isinstance(multi_window, dict) and multi_window.get("enabled"):
                model_multi_window_summary[key] = multi_window

        if model_multi_window_summary:
            multi_window_ranking = save_global_multi_window_ranking(
                model_multi_window_summary,
                output_path=os.path.join(REPORT_DIR, "multi_window_model_ranking.json"),
                ranking_metric=MULTI_WINDOW_RANKING_METRIC,
            )

    summary: Dict[str, Any] = {
        "direct_fit": len(prepared_main["X_direct_fit_model"]),
        "direct_val": len(prepared_main["X_direct_val"]),
        "direct_test": len(prepared_main["X_direct_test_model"]),
        "range_fit": len(prepared_main["X_range_fit_model"]),
        "range_val": len(prepared_main["X_range_val"]),
        "range_test": len(prepared_main["X_range_test_model"]),
        "features_direct": prepared_main["X_direct_fit_model"].shape[1],
        "features_range": prepared_main["X_range_fit_model"].shape[1],
        "backtest_rows": len(main_result["backtest_df"]),
        "direct_composition_profile": main_result["direct_composition_profile"],
        "direct_composition_config": main_result["direct_composition_config"],
        "direct_strategy": main_result["direct_strategy"],
        "direct_strategy_robustness": main_result.get("direct_strategy_robustness", {}),
        "robustness_classification": main_result.get("robustness_classification", {}),
        "robustness_status": dict(main_result.get("robustness_classification", {}) or {}).get("robustness_status"),
        "disabled_by_robustness": bool(dict(main_result.get("robustness_classification", {}) or {}).get("disabled_by_robustness", False)),
        "robustness_disable_reason": dict(main_result.get("robustness_classification", {}) or {}).get("robustness_disable_reason"),
        "selection_eligibility": bool(dict(main_result.get("robustness_classification", {}) or {}).get("selection_eligibility", True)),
        "final_holdout_safeguard_applied": bool(dict(main_result.get("robustness_classification", {}) or {}).get("final_holdout_safeguard_applied", False)),
        "final_holdout_guard_applied": main_final_holdout_guard_applied,
        "final_holdout_guard_reason": main_final_holdout_guard_reason,
        "final_holdout_delta_vs_baseline": main_final_holdout_delta_vs_baseline,
        "final_holdout_candidate_before_guard": main_final_holdout_candidate_before_guard,
        "final_holdout_candidate_after_guard": main_final_holdout_candidate_after_guard,
        "main_selection_relaxed_rule_applied": main_selection_relaxed_rule_applied,
        "main_selection_final_ranking_reason": main_selection_final_ranking_reason,
        "main_selection_baseline_overridden": main_selection_baseline_overridden,
        "main_selection_candidate_type": main_selection_candidate_type,
        "main_persistence_promotion_applied": main_persistence_promotion_applied,
        "main_persistence_promotable_candidate_count": main_persistence_promotable_candidate_count,
        "main_persistence_promotable_non_baseline_count": main_persistence_promotable_non_baseline_count,
        "main_persistence_baseline_excluded_from_promotion": main_persistence_baseline_excluded_from_promotion,
        "raw_model_metrics": main_raw_model_metrics,
        **main_raw_model_metrics,
        "raw_model_candidate_type": main_raw_model_candidate_type,
        "raw_model_used_before_guard": main_raw_model_used_before_guard,
        "guarded_candidate_type": main_guarded_candidate_type,
        "guarded_candidate_after_guard": main_guarded_candidate_after_guard,
        "overfitting_diagnostics": main_overfitting_diagnostics,
        **main_overfitting_metrics,
        "range_calibration": main_result["range_calibration"],
        "backtest_summary": main_result["backtest_summary"],
        "backtest_points": main_result["backtest_summary"].get("backtest_points"),
        "direction_points": main_result["backtest_summary"].get("direction_points"),
        "accuracy_metrics": main_result.get("accuracy_metrics", {}),
        "direction_accuracy_pct": main_result.get("accuracy_metrics", {}).get("direction_accuracy_pct"),
        "sign_accuracy_pct": main_result.get("accuracy_metrics", {}).get("sign_accuracy_pct"),
        "multi_window": main_result.get("multi_window", {}),
        "practical_selection_registry": practical_selection_registry,
        "practical_ranking_included_models": practical_ranking_included_models,
        "practical_ranking_excluded_models": practical_ranking_excluded_models,
        "live": live_result,
    }
    if multi_models_summary:
        summary["multi_models"] = multi_models_summary
    if multi_window_ranking is not None:
        summary["multi_window_model_ranking"] = multi_window_ranking

    return summary
