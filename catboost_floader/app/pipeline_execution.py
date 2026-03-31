from __future__ import annotations

import os
from typing import Any, Dict

import pandas as pd

from catboost_floader.core.config import (
    BACKTEST_DIR,
    DIRECT_CATBOOST_PARAMS,
    ENABLE_MULTI_WINDOW_EVALUATION,
    EVALUATION_WINDOW_COUNT,
    EVALUATION_WINDOW_SIZE,
    EVALUATION_WINDOW_STEP,
    MODEL_DIR,
    RANGE_HIGH_CATBOOST_PARAMS,
    RANGE_LOW_CATBOOST_PARAMS,
    REPORT_DIR,
    apply_cpu_worker_limits,
)
from catboost_floader.core.utils import ensure_dirs, save_json
from catboost_floader.evaluation.backtest import run_backtest
from catboost_floader.evaluation.multi_window import evaluate_model_multi_window
from catboost_floader.models.confidence import fit_error_calibrator
from catboost_floader.models.direct import train_direct_model
from catboost_floader.models.range import train_range_model

from catboost_floader.app.direct_robustness import _direct_strategy_robustness_payload
from catboost_floader.app.direct_strategy import _select_direct_strategy
from catboost_floader.app.holdout_safeguard import _apply_main_holdout_safeguard
from catboost_floader.app.pipeline_preparation import (
    _calibrate_range_model,
    _export_raw_model_artifacts,
    _save_feature_importance,
)
from catboost_floader.app.pipeline_utils import _prepare_catboost_params
from catboost_floader.app.robustness_regime import _classify_robustness_regime


def _initialize_multi_model_worker(thread_count: int) -> None:
    apply_cpu_worker_limits(thread_count, mark_outer_parallel=True)


def _run_pipeline_bundle(
    prepared: Dict[str, Any],
    *,
    direct_params=None,
    range_low_params=None,
    range_high_params=None,
    direct_composition_profile: str | None = None,
    catboost_thread_count: int | None = None,
    model_key: str = "main_direct_pipeline",
    enable_multi_window_evaluation: bool = ENABLE_MULTI_WINDOW_EVALUATION,
    evaluation_window_count: int = EVALUATION_WINDOW_COUNT,
    evaluation_window_size: int = EVALUATION_WINDOW_SIZE,
    evaluation_window_step: int = EVALUATION_WINDOW_STEP,
    model_dir: str = MODEL_DIR,
    report_dir: str = REPORT_DIR,
    backtest_dir: str = BACKTEST_DIR,
) -> Dict[str, Any]:
    ensure_dirs([model_dir, report_dir, backtest_dir])

    feature_stats = prepared["feature_stats"]
    save_json(feature_stats, os.path.join(model_dir, "feature_stats.json"))

    direct_params_prepared = _prepare_catboost_params(direct_params, DIRECT_CATBOOST_PARAMS, catboost_thread_count)
    range_low_params_prepared = _prepare_catboost_params(range_low_params, RANGE_LOW_CATBOOST_PARAMS, catboost_thread_count)
    range_high_params_prepared = _prepare_catboost_params(range_high_params, RANGE_HIGH_CATBOOST_PARAMS, catboost_thread_count)

    direct_model = train_direct_model(
        prepared["X_direct_fit_model"],
        prepared["y_direct_fit"],
        params=direct_params_prepared,
        composition_profile=direct_composition_profile,
        save=False,
    )
    direct_strategy = _select_direct_strategy(direct_model, prepared["X_direct_val"], prepared["y_direct_val"])
    direct_model.strategy = direct_strategy

    range_model = train_range_model(
        prepared["X_range_fit_model"],
        prepared["y_range_fit"],
        low_params=range_low_params_prepared,
        high_params=range_high_params_prepared,
        save=False,
    )
    range_calibration = _calibrate_range_model(
        range_model,
        direct_model,
        prepared["X_range_val"],
        prepared["X_direct_val"],
        prepared["y_direct_val"],
    )
    range_model.calibration = range_calibration

    direct_strategy, holdout_safeguard = _apply_main_holdout_safeguard(
        model_key=model_key,
        direct_model=direct_model,
        direct_strategy=direct_strategy,
        X_holdout_full=prepared["X_direct_test"],
        y_holdout=prepared["y_direct_test"],
    )
    direct_model.strategy = direct_strategy
    direct_strategy_robustness = _direct_strategy_robustness_payload(direct_strategy)
    direct_strategy_robustness["final_holdout_safeguard"] = holdout_safeguard

    direct_model.save(os.path.join(model_dir, "direct_model"))
    range_model.save(os.path.join(model_dir, "range_model"))
    _save_feature_importance(
        direct_model,
        range_model,
        list(prepared["X_direct_fit_model"].columns),
        list(prepared["X_range_fit_model"].columns),
        report_dir=report_dir,
    )

    train_pred = pd.Series(direct_model.predict(prepared["X_direct_fit"]))
    calibrator = fit_error_calibrator(
        prepared["X_direct_fit_model"],
        train_pred,
        prepared["y_direct_fit"]["target_return"],
        save_path=os.path.join(model_dir, "error_calibrator.pkl"),
    )

    backtest_df, backtest_summary = run_backtest(
        direct_features=prepared["X_direct_test"].reset_index(drop=True),
        range_features=prepared["X_range_test"].reset_index(drop=True),
        direct_targets=prepared["y_direct_test"].reset_index(drop=True),
        range_targets=prepared["y_range_test"].reset_index(drop=True),
        direct_model=direct_model,
        range_model=range_model,
        error_calibrator=calibrator,
        direct_feature_stats=feature_stats,
        output_dir=backtest_dir,
    )
    backtest_summary["direct_strategy"] = direct_strategy
    backtest_summary["direct_profile_selection"] = direct_strategy.get("profile_evaluations", [])
    backtest_summary["direct_strategy_robustness"] = direct_strategy_robustness
    accuracy_metrics = dict(backtest_summary.get("accuracy_metrics", {}) or {})
    _export_raw_model_artifacts(backtest_df, backtest_dir)

    multi_window_summary: Dict[str, Any]
    if enable_multi_window_evaluation:
        multi_window_summary = evaluate_model_multi_window(
            backtest_df,
            output_dir=backtest_dir,
            model_key=model_key,
            window_count=evaluation_window_count,
            window_size=evaluation_window_size,
            window_step=evaluation_window_step,
        )
    else:
        multi_window_summary = {
            "model_key": model_key,
            "enabled": False,
            "window_config": {
                "evaluation_window_count": int(evaluation_window_count),
                "evaluation_window_size": int(evaluation_window_size),
                "evaluation_window_step": int(evaluation_window_step),
            },
            "windows": [],
            "aggregate_metrics": {},
            "artifacts": {},
        }

    robustness_classification = _classify_robustness_regime(
        model_key=model_key,
        backtest_summary=backtest_summary,
        multi_window_summary=multi_window_summary,
        final_holdout_safeguard=holdout_safeguard,
    )

    backtest_summary["robustness_classification"] = robustness_classification
    backtest_summary["robustness_status"] = robustness_classification.get("robustness_status")
    backtest_summary["disabled_by_robustness"] = bool(robustness_classification.get("disabled_by_robustness", False))
    backtest_summary["robustness_disable_reason"] = robustness_classification.get("robustness_disable_reason")
    backtest_summary["selection_eligibility"] = bool(robustness_classification.get("selection_eligibility", True))
    backtest_summary["final_holdout_safeguard_applied"] = bool(robustness_classification.get("final_holdout_safeguard_applied", False))
    save_json(backtest_summary, os.path.join(backtest_dir, "backtest_summary.json"))

    save_json(
        {
            "direct_strategy": direct_strategy,
            "direct_profile_selection": direct_strategy.get("profile_evaluations", []),
            "direct_strategy_robustness": direct_strategy_robustness,
            "robustness_classification": robustness_classification,
            "robustness_status": robustness_classification.get("robustness_status"),
            "disabled_by_robustness": bool(robustness_classification.get("disabled_by_robustness", False)),
            "robustness_disable_reason": robustness_classification.get("robustness_disable_reason"),
            "selection_eligibility": bool(robustness_classification.get("selection_eligibility", True)),
            "final_holdout_safeguard_applied": bool(robustness_classification.get("final_holdout_safeguard_applied", False)),
            "range_calibration": range_calibration,
            "rows": {
                "direct_fit": int(len(prepared["X_direct_fit_model"])),
                "direct_val": int(len(prepared["X_direct_val"])),
                "direct_test": int(len(prepared["X_direct_test_model"])),
                "range_fit": int(len(prepared["X_range_fit_model"])),
                "range_val": int(len(prepared["X_range_val"])),
                "range_test": int(len(prepared["X_range_test_model"])),
            },
            "feature_counts": {
                "direct": int(prepared["X_direct_fit_model"].shape[1]),
                "range": int(prepared["X_range_fit_model"].shape[1]),
            },
            "direct_composition_profile": getattr(direct_model, "composition_profile", direct_composition_profile),
            "direct_composition_config": getattr(direct_model, "composition_config", {}),
            "backtest_points": backtest_summary.get("backtest_points"),
            "direction_points": backtest_summary.get("direction_points"),
            "accuracy_metrics": accuracy_metrics,
            "direction_accuracy_pct": accuracy_metrics.get("direction_accuracy_pct"),
            "sign_accuracy_pct": accuracy_metrics.get("sign_accuracy_pct"),
            "multi_window": multi_window_summary,
        },
        os.path.join(backtest_dir, "pipeline_metadata.json"),
    )

    return {
        "feature_stats": feature_stats,
        "direct_model": direct_model,
        "direct_strategy": direct_strategy,
        "direct_strategy_robustness": direct_strategy_robustness,
        "robustness_classification": robustness_classification,
        "range_model": range_model,
        "range_calibration": range_calibration,
        "error_calibrator": calibrator,
        "backtest_df": backtest_df,
        "backtest_summary": backtest_summary,
        "accuracy_metrics": accuracy_metrics,
        "direct_composition_profile": getattr(direct_model, "composition_profile", direct_composition_profile),
        "direct_composition_config": getattr(direct_model, "composition_config", {}),
        "multi_window": multi_window_summary,
    }
