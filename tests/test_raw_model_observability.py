import pandas as pd

from catboost_floader.app.pipeline_execution import _build_raw_vs_guarded_trace_fields
from catboost_floader.app.pipeline_summary import _build_pipeline_summary
from catboost_floader.evaluation.backtest import _compute_raw_model_observability_metrics


def test_compute_raw_model_observability_metrics_exports_required_fields():
    close = pd.Series([100.0, 100.0, 100.0, 100.0])
    target_return = pd.Series([0.01, -0.02, 0.03, -0.01])
    target_future_close = close * (1.0 + target_return)
    raw_pred_return = pd.Series([0.012, -0.018, 0.025, -0.015])
    baseline_persistence_price = close * (1.0 + pd.Series([0.0, 0.0, 0.0, 0.0]))

    metrics = _compute_raw_model_observability_metrics(
        close=close,
        target_future_close=target_future_close,
        target_return=target_return,
        raw_pred_return=raw_pred_return,
        baseline_persistence_price=baseline_persistence_price,
        deadband=0.001,
    )

    required_fields = {
        "raw_model_MAE",
        "raw_model_sign_acc",
        "raw_model_sign_acc_pct",
        "raw_model_direction_acc",
        "raw_model_direction_acc_pct",
        "raw_model_delta_vs_baseline",
        "raw_model_mean_delta_vs_baseline",
        "raw_model_std_delta_vs_baseline",
        "raw_model_win_rate_vs_baseline",
    }
    assert required_fields.issubset(metrics.keys())
    assert float(metrics["raw_model_sign_acc"]) == 1.0
    assert float(metrics["raw_model_direction_acc"]) == 1.0
    assert float(metrics["raw_model_delta_vs_baseline"]) > 0.0
    assert 0.0 <= float(metrics["raw_model_win_rate_vs_baseline"]) <= 1.0


def test_build_raw_vs_guarded_trace_fields_compares_before_after_guard():
    trace = _build_raw_vs_guarded_trace_fields(
        direct_strategy={"type": "baseline_only"},
        holdout_safeguard={
            "final_holdout_candidate_before_guard": {"type": "blend"},
            "final_holdout_candidate_after_guard": {"type": "baseline_only"},
            "final_holdout_guard_applied": True,
        },
    )

    assert trace["raw_model_candidate_type"] == "model_only"
    assert trace["raw_model_used_before_guard"] is True
    assert trace["guarded_candidate_type"] == "baseline_only"
    assert trace["guarded_candidate_after_guard"] is True


def test_pipeline_summary_exposes_raw_model_metrics_and_trace_fields():
    prepared_main = {
        "X_direct_fit_model": pd.DataFrame({"x": [1.0, 2.0]}),
        "X_direct_val": pd.DataFrame({"x": [3.0]}),
        "X_direct_test_model": pd.DataFrame({"x": [4.0]}),
        "X_range_fit_model": pd.DataFrame({"x": [1.0, 2.0]}),
        "X_range_val": pd.DataFrame({"x": [3.0]}),
        "X_range_test_model": pd.DataFrame({"x": [4.0]}),
    }

    main_result = {
        "backtest_df": pd.DataFrame({"x": [1.0]}),
        "direct_composition_profile": "default",
        "direct_composition_config": {},
        "direct_strategy": {"type": "baseline_only"},
        "direct_strategy_robustness": {},
        "robustness_classification": {
            "robustness_status": "near_baseline",
            "disabled_by_robustness": False,
            "selection_eligibility": True,
            "final_holdout_safeguard_applied": False,
        },
        "final_holdout_guard_applied": True,
        "final_holdout_guard_reason": "clear_underperformance_vs_persistence",
        "final_holdout_delta_vs_baseline": -0.01,
        "final_holdout_candidate_before_guard": {"type": "blend"},
        "final_holdout_candidate_after_guard": {"type": "baseline_only"},
        "main_selection_relaxed_rule_applied": False,
        "main_selection_final_ranking_reason": "mae_better",
        "main_selection_baseline_overridden": False,
        "main_selection_candidate_type": "baseline_only",
        "main_persistence_promotion_applied": False,
        "main_persistence_promotable_candidate_count": 0,
        "main_persistence_promotable_non_baseline_count": 0,
        "main_persistence_baseline_excluded_from_promotion": False,
        "raw_model_metrics": {
            "raw_model_MAE": 0.12,
            "raw_model_sign_acc": 0.55,
            "raw_model_sign_acc_pct": 55.0,
            "raw_model_direction_acc": 0.56,
            "raw_model_direction_acc_pct": 56.0,
            "raw_model_delta_vs_baseline": 0.03,
            "raw_model_mean_delta_vs_baseline": 0.01,
            "raw_model_std_delta_vs_baseline": 0.02,
            "raw_model_win_rate_vs_baseline": 0.6,
        },
        "raw_model_candidate_type": "model_only",
        "raw_model_used_before_guard": True,
        "guarded_candidate_type": "baseline_only",
        "guarded_candidate_after_guard": True,
        "selection_effective_score": 101.2,
        "effective_penalty_value": 3.1,
        "penalty_components": {"risk_score": 0.6},
        "holdout_weight_used": 0.62,
        "validation_weight_used": 0.38,
        "holdout_proxy_mae": 98.1,
        "overfitting_diagnostics": {"overfit_status": "none", "overfit_reason": "within_thresholds"},
        "range_calibration": {},
        "backtest_summary": {
            "backtest_points": 1,
            "direction_points": 1,
            "overfitting_diagnostics": {"overfit_status": "none", "overfit_reason": "within_thresholds"},
            "selection_effective_score": 101.2,
            "effective_penalty_value": 3.1,
            "penalty_components": {"risk_score": 0.6},
            "holdout_weight_used": 0.62,
            "validation_weight_used": 0.38,
            "holdout_proxy_mae": 98.1,
        },
        "accuracy_metrics": {"direction_accuracy_pct": 55.0, "sign_accuracy_pct": 54.0},
        "multi_window": {"enabled": False},
    }

    multi_models_summary = {
        "60min_3h": {
            "robustness_classification": {"robustness_status": "robust", "selection_eligibility": True},
            "overfit_status": "moderate",
            "overfit_reason": "sign_gap_train_holdout_ge_0_07",
            "raw_model_MAE": 0.2,
            "raw_model_sign_acc": 0.58,
            "raw_model_sign_acc_pct": 58.0,
            "raw_model_direction_acc": 0.59,
            "raw_model_direction_acc_pct": 59.0,
            "raw_model_delta_vs_baseline": 0.01,
            "raw_model_mean_delta_vs_baseline": 0.005,
            "raw_model_std_delta_vs_baseline": 0.03,
            "raw_model_win_rate_vs_baseline": 0.52,
            "raw_model_candidate_type": "model_only",
            "raw_model_used_before_guard": True,
            "guarded_candidate_type": "baseline_only",
            "guarded_candidate_after_guard": True,
            "selection_effective_score": 115.0,
            "effective_penalty_value": 4.2,
            "penalty_components": {"risk_score": 0.7},
            "holdout_weight_used": 0.62,
            "validation_weight_used": 0.38,
            "holdout_proxy_mae": 110.0,
            "metrics": {
                "raw_model_MAE": 0.2,
                "raw_model_sign_acc": 0.58,
                "raw_model_sign_acc_pct": 58.0,
                "raw_model_direction_acc": 0.59,
                "raw_model_direction_acc_pct": 59.0,
                "raw_model_delta_vs_baseline": 0.01,
                "raw_model_mean_delta_vs_baseline": 0.005,
                "raw_model_std_delta_vs_baseline": 0.03,
                "raw_model_win_rate_vs_baseline": 0.52,
            },
        }
    }

    summary = _build_pipeline_summary(
        prepared_main=prepared_main,
        main_result=main_result,
        multi_models_summary=multi_models_summary,
        live_result={},
    )

    assert float(summary["raw_model_MAE"]) == 0.12
    assert summary["raw_model_candidate_type"] == "model_only"
    assert summary["guarded_candidate_type"] == "baseline_only"
    assert summary["guarded_candidate_after_guard"] is True
    assert float(summary["effective_penalty_value"]) == 3.1
    assert float(summary["holdout_weight_used"]) == 0.62

    registry = summary["practical_selection_registry"]
    assert float(registry["main_direct_pipeline"]["raw_model_MAE"]) == 0.12
    assert float(registry["60min_3h"]["raw_model_MAE"]) == 0.2
    assert float(registry["main_direct_pipeline"]["effective_penalty_value"]) == 3.1
    assert float(registry["60min_3h"]["holdout_weight_used"]) == 0.62
