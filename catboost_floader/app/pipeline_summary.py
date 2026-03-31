from __future__ import annotations

import os
from typing import Any, Dict

from catboost_floader.core.config import (
    ENABLE_MULTI_WINDOW_EVALUATION,
    MULTI_WINDOW_RANKING_METRIC,
    REPORT_DIR,
)
from catboost_floader.evaluation.multi_window import save_global_multi_window_ranking


def _build_pipeline_summary(
    *,
    prepared_main: Dict[str, Any],
    main_result: Dict[str, Any],
    multi_models_summary: Dict[str, Dict[str, Any]],
    live_result: Dict[str, Any],
) -> Dict[str, Any]:
    practical_selection_registry: dict[str, Dict[str, Any]] = {}
    main_classification = dict(main_result.get("robustness_classification", {}) or {})
    practical_selection_registry["main_direct_pipeline"] = {
        "robustness_status": main_classification.get("robustness_status"),
        "disabled_by_robustness": bool(main_classification.get("disabled_by_robustness", False)),
        "robustness_disable_reason": main_classification.get("robustness_disable_reason"),
        "selection_eligibility": bool(main_classification.get("selection_eligibility", True)),
        "final_holdout_safeguard_applied": bool(main_classification.get("final_holdout_safeguard_applied", False)),
    }
    for key, model_summary in multi_models_summary.items():
        model_classification = dict(model_summary.get("robustness_classification", {}) or {})
        practical_selection_registry[key] = {
            "robustness_status": model_classification.get("robustness_status"),
            "disabled_by_robustness": bool(model_classification.get("disabled_by_robustness", False)),
            "robustness_disable_reason": model_classification.get("robustness_disable_reason"),
            "selection_eligibility": bool(model_classification.get("selection_eligibility", True)),
            "final_holdout_safeguard_applied": bool(model_classification.get("final_holdout_safeguard_applied", False)),
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
