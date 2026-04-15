import json
from pathlib import Path

from catboost_floader.diagnostics import model_snapshot as ms
from frontend.services import loaders as ld


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_outputs_dir_prefers_package_outputs(tmp_path: Path):
    repo_outputs = tmp_path / "outputs"
    repo_outputs.mkdir(parents=True)

    pkg_outputs = tmp_path / "catboost_floader" / "outputs"
    _write_json(pkg_outputs / "reports" / "pipeline_summary.json", {"ok": True})
    _write_json(pkg_outputs / "backtest_results" / "backtest_summary.json", {"ok": True})

    resolved = ld._resolve_outputs_dir(tmp_path)

    assert resolved == pkg_outputs


def test_build_model_record_normalizes_summary_and_selection_fields(tmp_path: Path):
    outputs_dir = tmp_path / "catboost_floader" / "outputs"
    backtest_summary = {
        "direct_model": {
            "MAE": 10.0,
            "RMSE": 12.0,
            "MAPE": 1.5,
            "sign_accuracy": 0.55,
            "sign_accuracy_pct": 55.0,
            "sign_confusion": {
                "true_positive": 21,
                "true_negative": 18,
                "false_positive": 11,
                "false_negative": 9,
            },
        },
        "direct_baselines": {"persistence": {"MAE": 12.0}},
        "accuracy_metrics": {
            "direction_accuracy": 0.52,
            "direction_accuracy_pct": 52.0,
            "sign_accuracy_pct": 55.0,
            "sign_confusion": {
                "true_positive": 21,
                "true_negative": 18,
                "false_positive": 11,
                "false_negative": 9,
            },
        },
        "raw_model_metrics": {
            "raw_model_MAE": 9.5,
            "raw_model_delta_vs_baseline": 2.5,
            "raw_model_sign_acc_pct": 58.0,
            "raw_model_direction_acc_pct": 57.0,
        },
        "robustness_status": "robust_winner",
        "selection_eligibility": True,
        "overfitting_diagnostics": {
            "overfit_status": "moderate",
            "overfit_reason": "sign_gap_train_holdout_ge_0_07",
            "train_sign_acc": 0.61,
            "val_sign_acc": 0.57,
            "holdout_sign_acc": 0.54,
        },
        "raw_model_candidate_type": "model_only",
        "raw_model_used_before_guard": True,
        "guarded_candidate_type": "blend",
        "guarded_candidate_after_guard": False,
        "direct_strategy": {
            "type": "blend",
            "validation_mae": 11.0,
            "selection_pool": "robustness_gate_pass",
            "composition_profile": "default",
            "profile_selection_mode": "validation_plus_multi_window_robustness",
            "robustness_metrics": {
                "mean_delta_vs_baseline": 1.2,
                "std_delta_vs_baseline": 0.4,
                "win_rate_vs_baseline": 0.8,
                "best_window_delta_vs_baseline": 2.1,
                "worst_window_delta_vs_baseline": -0.3,
            },
        },
        "final_holdout_guard_reason": "not_triggered",
    }
    pipeline_metadata = {"rows": {"direct_test": 100}}

    _write_json(outputs_dir / "backtest_results" / "backtest_summary.json", backtest_summary)
    _write_json(outputs_dir / "backtest_results" / "pipeline_metadata.json", pipeline_metadata)

    record = ld._build_model_record(
        outputs_dir=outputs_dir,
        model_key="main_direct_pipeline",
        summary_seed={"recommendation_bucket": "Preferred"},
        pipeline_summary={"multi_models": {}},
    )

    assert record["summary"]["MAE"] == 10.0
    assert record["summary"]["delta_vs_baseline"] == 2.0
    assert record["summary"]["robustness_status"] == "robust_winner"
    assert record["raw_model"]["raw_model_delta_vs_baseline"] == 2.5
    assert record["selection"]["guarded_candidate_type"] == "blend"
    assert record["selection"]["final_holdout_guard_reason"] == "not_triggered"
    assert record["registry"]["sign_tp"] == 21
    assert record["registry"]["sign_tn"] == 18
    assert record["registry"]["sign_fp"] == 11
    assert record["registry"]["sign_fn"] == 9
    assert record["overfitting"]["train_sign_acc_pct"] == 61.0
    assert record["overfitting"]["val_sign_acc_pct"] == 57.0
    assert record["overfitting"]["holdout_sign_acc_pct"] == 54.0
    assert record["registry"]["recommendation_bucket"] == "Preferred"


def test_build_model_snapshot_exposes_sign_confusion_and_overfit_sign_percentages(tmp_path: Path, monkeypatch):
    outputs_dir = tmp_path / "catboost_floader" / "outputs"
    backtest_summary = {
        "direct_model": {
            "MAE": 8.0,
            "sign_accuracy": 0.58,
            "sign_accuracy_pct": 58.0,
            "sign_confusion": {
                "true_positive": 30,
                "true_negative": 24,
                "false_positive": 8,
                "false_negative": 6,
            },
        },
        "direct_baselines": {"persistence": {"MAE": 9.0}},
        "accuracy_metrics": {"sign_accuracy_pct": 58.0},
        "overfitting_diagnostics": {
            "train_sign_acc": 0.64,
            "val_sign_acc": 0.59,
            "holdout_sign_acc": 0.58,
            "overfit_status": "mild",
            "overfit_reason": "sign_gap_train_val_ge_0_03",
        },
    }

    _write_json(outputs_dir / "backtest_results" / "backtest_summary.json", backtest_summary)
    _write_json(outputs_dir / "backtest_results" / "pipeline_metadata.json", {"rows": {"direct_test": 68}})

    monkeypatch.setattr(ms, "load_model_backtest_summary", lambda model_key: backtest_summary)
    monkeypatch.setattr(ms, "load_model_pipeline_metadata", lambda model_key: {"rows": {"direct_test": 68}})
    monkeypatch.setattr(ms, "load_model_multi_window_summary", lambda model_key: {})
    monkeypatch.setattr(ms, "load_model_comparison_vs_baselines", lambda model_key: {})
    monkeypatch.setattr(
        ms,
        "model_artifact_paths",
        lambda model_key: {"backtest_summary": str(outputs_dir / "backtest_results" / "backtest_summary.json")},
    )

    snapshot = ms.build_model_snapshot("main_direct_pipeline")

    assert snapshot["registry"]["sign_tp"] == 30
    assert snapshot["registry"]["sign_tn"] == 24
    assert snapshot["registry"]["sign_fp"] == 8
    assert snapshot["registry"]["sign_fn"] == 6
    assert snapshot["overfitting"]["train_sign_acc_pct"] == 64.0
    assert snapshot["overfitting"]["val_sign_acc_pct"] == 59.0
    assert snapshot["overfitting"]["holdout_sign_acc_pct"] == 58.0


def test_build_model_records_collects_main_and_multi_models(tmp_path: Path):
    outputs_dir = tmp_path / "catboost_floader" / "outputs"
    _write_json(
        outputs_dir / "backtest_results" / "backtest_summary.json",
        {
            "direct_model": {"MAE": 10.0},
            "direct_baselines": {"persistence": {"MAE": 11.0}},
            "accuracy_metrics": {"sign_accuracy_pct": 50.0, "direction_accuracy_pct": 49.0},
        },
    )
    _write_json(
        outputs_dir / "backtest_results" / "multi_models" / "60min_3h" / "backtest_summary.json",
        {
            "direct_model": {"MAE": 8.0},
            "direct_baselines": {"persistence": {"MAE": 10.0}},
            "accuracy_metrics": {"sign_accuracy_pct": 54.0, "direction_accuracy_pct": 53.0},
            "robustness_status": "robust_winner",
            "selection_eligibility": True,
        },
    )

    records = ld._build_model_records(
        outputs_dir,
        {
            "multi_models": {
                "60min_3h": {
                    "robustness_status": "robust_winner",
                    "selection_eligibility": True,
                }
            }
        },
    )

    assert "main_direct_pipeline" in records
    assert "60min_3h" in records
    assert records["60min_3h"]["registry"]["model_name"] == "60min_3h"