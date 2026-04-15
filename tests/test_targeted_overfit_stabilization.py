import numpy as np
import pandas as pd

from catboost_floader.selection import direct_strategy as ds


class _FakeDirectModel:
    def __init__(
        self,
        raw_pred: np.ndarray,
        profile: str = "default",
        expectation: np.ndarray | None = None,
        confidence: np.ndarray | float = 0.9,
    ):
        self.feature_names = ["feat", "return_1"]
        self.composition_profile = profile
        self.composition_config = {"profile_enabled": True, "profile_fallbacks": []}
        self.direction_model = type("_DirectionStub", (), {"calibration": {}})()
        self._raw_pred = np.asarray(raw_pred, dtype=float)
        self._expectation = None if expectation is None else np.asarray(expectation, dtype=float)
        self._confidence = confidence

    def predict_details(self, X, composition_profile=None, composition_config=None):
        n = len(X)
        raw = self._raw_pred
        if len(raw) != n:
            raw = np.resize(raw, n)
        expectation = self._expectation
        if expectation is None:
            scale = max(float(np.max(np.abs(raw))), 1e-8)
            expectation = np.clip(raw / scale, -1.0, 1.0)
        elif len(expectation) != n:
            expectation = np.resize(expectation, n)

        confidence = self._confidence
        if np.isscalar(confidence):
            confidence_arr = np.full(n, float(confidence), dtype=float)
        else:
            confidence_arr = np.asarray(confidence, dtype=float)
            if len(confidence_arr) != n:
                confidence_arr = np.resize(confidence_arr, n)

        probs = np.full((n, 3), 0.0, dtype=float)
        labels = np.sign(raw).astype(int)
        top_idx = np.where(labels > 0, 2, np.where(labels < 0, 0, 1))
        for idx in range(n):
            conf = float(np.clip(confidence_arr[idx], 1.0 / 3.0, 1.0))
            remainder = max(0.0, 1.0 - conf)
            probs[idx, :] = remainder / 2.0
            probs[idx, top_idx[idx]] = conf
        return {
            "raw_pred_return": raw,
            "direction_label": labels,
            "direction_expectation": expectation,
            "direction_proba": probs,
            "direction_confidence_threshold": 0.6,
        }


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_calibrate_direction_thresholds",
        lambda **kwargs: {
            "applied": False,
            "reason": "test_override",
            "applies_to_profiles": ["main_direct_pipeline", "default"],
        },
    )
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: [None])
    monkeypatch.setattr(
        ds,
        "_compute_direct_candidate_multi_window",
        lambda **kwargs: {
            "aggregate_metrics": {
                "window_count": 5,
                "mean_delta_vs_baseline": 1.0,
                "std_delta_vs_baseline": 0.5,
                "win_rate_vs_baseline": 0.8,
                "mean_sign_accuracy_pct": 55.0,
            }
        },
    )
    monkeypatch.setattr(
        ds,
        "_extract_robustness_metrics",
        lambda summary: dict(summary.get("aggregate_metrics", {})),
    )
    monkeypatch.setattr(ds, "_direct_strategy_passes_robustness", lambda _metrics: (True, []))



def _build_data(n: int = 30):
    close = np.full(n, 100.0, dtype=float)
    target_return = np.full(n, 0.01, dtype=float)
    # Baseline is only slightly worse than model-only in raw validation MAE.
    baseline_return = np.full(n, 0.01031, dtype=float)

    X_val_full = pd.DataFrame(
        {
            "close": close,
            "feat": np.arange(n, dtype=float),
            "return_1": baseline_return,
            "ret_mean_6": np.zeros(n, dtype=float),
            "ret_mean_12": np.zeros(n, dtype=float),
        }
    )
    y_val = pd.DataFrame(
        {
            "target_return": target_return,
            "target_future_close": close * (1.0 + target_return),
        }
    )
    return X_val_full, y_val



def test_target_model_applies_overfit_penalty_and_prefers_stabler_candidate(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        ds,
        "_direct_strategy_candidates",
        lambda _cfg: [
            {"type": "baseline_only", "alpha": 0.0, "baseline": "persistence"},
            {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        ],
    )
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 1.25,
            "sign_gap_train_holdout": 0.24,
            "mae_gap_train_holdout": 80.0,
            "holdout_MAE": 400.0,
        },
    )

    X_val_full, y_val = _build_data()
    # model-only MAE is slightly better than baseline before stabilization.
    direct_model = _FakeDirectModel(np.full(len(X_val_full), 0.0103, dtype=float), profile="default")

    strategy = ds._select_direct_strategy(
        direct_model,
        X_val_full,
        y_val,
        model_key="60min_3h",
    )

    assert strategy["stabilization_targeted_model"] is True
    assert strategy["stabilization_applied"] is True
    assert strategy["stabilization_overfit_status"] == "severe"
    assert strategy["type"] == "baseline_only"
    assert float(strategy.get("stabilization_overfit_penalty_ratio", 0.0)) >= 0.0



def test_non_target_model_keeps_original_selection_behavior(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        ds,
        "_direct_strategy_candidates",
        lambda _cfg: [
            {"type": "baseline_only", "alpha": 0.0, "baseline": "persistence"},
            {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        ],
    )
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 1.25,
            "sign_gap_train_holdout": 0.24,
            "mae_gap_train_holdout": 80.0,
            "holdout_MAE": 400.0,
        },
    )

    X_val_full, y_val = _build_data()
    direct_model = _FakeDirectModel(np.full(len(X_val_full), 0.0103, dtype=float), profile="default")

    strategy = ds._select_direct_strategy(
        direct_model,
        X_val_full,
        y_val,
        model_key="1min_3h",
    )

    assert strategy["stabilization_targeted_model"] is False
    assert strategy["stabilization_applied"] is False
    assert strategy["type"] == "model_only"



def test_stabilization_config_overrides_cap_alpha_and_increase_conservatism():
    context = {
        "enabled": True,
        "severity": "severe",
        "policy": {
            "alpha_cap": 0.72,
            "confidence_bump": 0.03,
            "movement_scale_cap": 0.96,
            "expectation_deadband_floor": 0.03,
            "low_confidence_expectation_weight_cap": 0.75,
        },
    }
    cfg = {
        "label_confidence_threshold": 0.55,
        "movement_scale": 1.0,
        "expectation_deadband": 0.0,
        "low_confidence_sign_mode": "expectation",
        "low_confidence_expectation_weight": 1.0,
        "strategy_prefer_model_tolerance": 0.0005,
        "strategy_alpha_grid": [0.4, 0.55, 0.7, 0.85, 0.92],
    }

    out_cfg, payload = ds._apply_targeted_stabilization_to_strategy_config(cfg, context)

    assert payload["applied"] is True
    assert float(out_cfg["label_confidence_threshold"]) > 0.55
    assert float(out_cfg["movement_scale"]) <= 0.96
    assert float(out_cfg["expectation_deadband"]) >= 0.03
    assert float(out_cfg["low_confidence_expectation_weight"]) <= 0.75
    assert str(out_cfg["low_confidence_sign_mode"]) == "blend"
    assert max(float(a) for a in out_cfg["strategy_alpha_grid"]) <= 0.72 + 1e-12


def test_60min_6h_family_override_activates_below_global_sign_gap_threshold(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "none",
            "overfit_reason": "below_global_threshold",
            "holdout_overfit_ratio": 0.84,
            "sign_gap_train_holdout": 0.06,
            "mae_gap_train_holdout": 40.0,
            "holdout_MAE": 600.0,
            "holdout_delta_vs_baseline": 18.0,
        },
    )

    context = ds._resolve_target_overfit_stabilization_context("60min_6h")

    assert context["enabled"] is True
    assert context["severity"] == "moderate"
    assert context["policy"]["activation_sign_gap_min"] == 0.05
    assert context["policy"]["override_sources"] == ["60min_family"]


def test_15min_3h_severe_override_preserves_more_model_signal(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.79,
            "sign_gap_train_holdout": 0.17,
            "mae_gap_train_holdout": 55.0,
            "holdout_MAE": 440.0,
            "holdout_delta_vs_baseline": 6.4,
        },
    )
    context = ds._resolve_target_overfit_stabilization_context("15min_3h")
    cfg = {
        "label_confidence_threshold": 0.60,
        "movement_scale": 1.0,
        "expectation_deadband": 0.0,
        "low_confidence_sign_mode": "expectation",
        "low_confidence_expectation_weight": 1.0,
        "strategy_prefer_model_tolerance": 0.0005,
        "strategy_alpha_grid": [0.25, 0.4, 0.55, 0.7, 0.8, 0.85],
    }

    out_cfg, payload = ds._apply_targeted_stabilization_to_strategy_config(cfg, context)

    assert payload["applied"] is True
    assert str(out_cfg["low_confidence_sign_mode"]) == "expectation"
    assert float(out_cfg["label_confidence_threshold"]) == 0.615
    assert float(out_cfg["movement_scale"]) == 0.98
    assert float(out_cfg["expectation_deadband"]) == 0.015
    assert float(out_cfg["low_confidence_expectation_weight"]) == 0.9
    assert float(out_cfg["strategy_prefer_model_tolerance"]) == 0.0005
    assert max(float(a) for a in out_cfg["strategy_alpha_grid"]) == 0.8


def test_60min_family_penalty_shape_hits_aggressive_candidates_harder_than_15min(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.95,
            "sign_gap_train_holdout": 0.18,
            "mae_gap_train_holdout": 75.0,
            "holdout_MAE": 500.0,
            "holdout_delta_vs_baseline": 15.0,
        },
    )
    candidate_eval = {
        "strategy": {"type": "blend", "alpha": 0.85, "baseline": "persistence"},
        "validation_mae": 420.0,
        "relative_delta_vs_persistence": 0.03,
    }

    penalty_60min = ds._candidate_overfit_penalty_ratio(
        candidate_eval,
        ds._resolve_target_overfit_stabilization_context("60min_3h"),
    )
    penalty_15min = ds._candidate_overfit_penalty_ratio(
        candidate_eval,
        ds._resolve_target_overfit_stabilization_context("15min_3h"),
    )

    assert penalty_60min > penalty_15min


def test_holdout_weighted_selection_score_prefers_holdout_over_validation(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "mae_overfit_ratio_ge_1_10",
            "val_MAE": 420.0,
            "holdout_MAE": 540.0,
            "holdout_overfit_ratio": 1.32,
            "sign_gap_train_holdout": 0.16,
            "mae_gap_train_holdout": 80.0,
            "holdout_delta_vs_baseline": 22.0,
        },
    )
    context = ds._resolve_target_overfit_stabilization_context("60min_3h")
    candidate_eval = {
        "strategy": {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        "validation_mae": 430.0,
        "relative_delta_vs_persistence": 0.04,
    }

    penalty_payload = ds._candidate_overfit_penalty_payload(candidate_eval, context)
    score_payload = ds._candidate_effective_selection_score(
        candidate_eval,
        context,
        penalty_payload=penalty_payload,
    )

    assert score_payload["holdout_weight_used"] > score_payload["validation_weight_used"]
    assert score_payload["holdout_metric"] > float(candidate_eval["validation_mae"])
    assert score_payload["effective_score"] > score_payload["holdout_metric"] * score_payload["holdout_weight_used"]


def test_smooth_penalty_relief_preserves_strong_edge_and_low_alpha(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 1.34,
            "sign_gap_train_holdout": 0.18,
            "mae_gap_train_holdout": 85.0,
            "holdout_MAE": 510.0,
            "holdout_delta_vs_baseline": 18.0,
        },
    )
    context = ds._resolve_target_overfit_stabilization_context("60min_6h")

    aggressive_weak = ds._candidate_overfit_penalty_payload(
        {
            "strategy": {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
            "validation_mae": 425.0,
            "relative_delta_vs_persistence": 0.01,
        },
        context,
    )
    aggressive_strong = ds._candidate_overfit_penalty_payload(
        {
            "strategy": {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
            "validation_mae": 425.0,
            "relative_delta_vs_persistence": 0.08,
        },
        context,
    )
    conservative_strong = ds._candidate_overfit_penalty_payload(
        {
            "strategy": {"type": "blend", "alpha": 0.25, "baseline": "persistence"},
            "validation_mae": 425.0,
            "relative_delta_vs_persistence": 0.08,
        },
        context,
    )

    assert aggressive_strong["ratio"] < aggressive_weak["ratio"]
    assert conservative_strong["ratio"] < aggressive_strong["ratio"]


def test_prediction_stabilization_shrinks_aggressive_60min_candidate_before_ranking(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.85,
            "sign_gap_train_holdout": 0.22,
            "mae_gap_train_holdout": 70.0,
            "holdout_MAE": 500.0,
            "holdout_delta_vs_baseline": 18.0,
        },
    )
    context = ds._resolve_target_overfit_stabilization_context("60min_3h")
    strategy = ds._strategy_with_prediction_stabilization(
        {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        context,
    )
    candidate_return = np.array([0.030, -0.026, 0.024, -0.020], dtype=float)
    baseline_return = np.array([0.010, -0.009, 0.011, -0.010], dtype=float)
    direction_expectation = np.array([0.16, -0.14, 0.18, -0.12], dtype=float)
    direction_proba = np.array(
        [
            [0.58, 0.21, 0.21],
            [0.21, 0.21, 0.58],
            [0.61, 0.195, 0.195],
            [0.225, 0.225, 0.55],
        ],
        dtype=float,
    )

    stabilized_return, stats = ds.apply_direct_prediction_stabilization(
        candidate_return,
        baseline_return,
        direction_expectation=direction_expectation,
        direction_proba=direction_proba,
        direction_confidence_threshold=0.6,
        strategy=strategy,
    )

    assert stats["applied"] is True
    assert stats["mean_abs_deviation_after"] < stats["mean_abs_deviation_before"]
    assert stats["mean_abs_return_after"] < stats["mean_abs_return_before"]
    assert np.all(np.abs(stabilized_return) <= np.abs(candidate_return) + 1e-12)
    assert stats["mean_abs_deviation_after"] > stats["mean_abs_deviation_after_confidence"] * 0.85


def test_prediction_stabilization_is_gentler_for_15min_than_60min(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.82,
            "sign_gap_train_holdout": 0.18,
            "mae_gap_train_holdout": 60.0,
            "holdout_MAE": 450.0,
            "holdout_delta_vs_baseline": 10.0,
        },
    )
    candidate_return = np.array([0.028, -0.024, 0.022, -0.018], dtype=float)
    baseline_return = np.array([0.010, -0.009, 0.011, -0.010], dtype=float)
    direction_expectation = np.array([0.20, -0.18, 0.16, -0.15], dtype=float)
    direction_proba = np.array(
        [
            [0.57, 0.215, 0.215],
            [0.215, 0.215, 0.57],
            [0.59, 0.205, 0.205],
            [0.22, 0.22, 0.56],
        ],
        dtype=float,
    )

    strategy_60 = ds._strategy_with_prediction_stabilization(
        {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        ds._resolve_target_overfit_stabilization_context("60min_12h"),
    )
    strategy_15 = ds._strategy_with_prediction_stabilization(
        {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        ds._resolve_target_overfit_stabilization_context("15min_3h"),
    )

    _, stats_60 = ds.apply_direct_prediction_stabilization(
        candidate_return,
        baseline_return,
        direction_expectation=direction_expectation,
        direction_proba=direction_proba,
        direction_confidence_threshold=0.6,
        strategy=strategy_60,
    )
    _, stats_15 = ds.apply_direct_prediction_stabilization(
        candidate_return,
        baseline_return,
        direction_expectation=direction_expectation,
        direction_proba=direction_proba,
        direction_confidence_threshold=0.6,
        strategy=strategy_15,
    )

    ratio_60 = stats_60["mean_abs_deviation_after"] / stats_60["mean_abs_deviation_before"]
    ratio_15 = stats_15["mean_abs_deviation_after"] / stats_15["mean_abs_deviation_before"]
    assert ratio_60 < ratio_15


def test_60min_family_prediction_override_is_softer_than_previous_stage(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.88,
            "sign_gap_train_holdout": 0.19,
            "mae_gap_train_holdout": 72.0,
            "holdout_MAE": 520.0,
            "holdout_delta_vs_baseline": 14.0,
        },
    )

    strategy = ds._strategy_with_prediction_stabilization(
        {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
        ds._resolve_target_overfit_stabilization_context("60min_6h"),
    )
    policy = dict(strategy.get("prediction_stabilization", {}) or {})

    assert policy["low_confidence_shrink_max"] == 0.20
    assert policy["deviation_soft_limit_multiplier"] == 1.55
    assert policy["confidence_threshold_buffer"] == 0.07
    assert policy["signal_confidence_weight"] == 0.65
    assert policy["signal_expectation_weight"] == 0.35
    assert policy["baseline_mean_abs_weight"] == 0.40


def test_selected_strategy_carries_prediction_stabilization_metadata(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(ds, "_direct_strategy_candidates", lambda _cfg: [{"type": "model_only", "alpha": 1.0, "baseline": "persistence"}])
    monkeypatch.setattr(ds, "_apply_persistence_guard", lambda **kwargs: None)
    monkeypatch.setattr(
        ds,
        "_load_previous_overfitting_diagnostics",
        lambda _key: {
            "overfit_status": "severe",
            "overfit_reason": "sign_gap_train_holdout_ge_0_12",
            "holdout_overfit_ratio": 0.84,
            "sign_gap_train_holdout": 0.30,
            "mae_gap_train_holdout": 80.0,
            "holdout_MAE": 550.0,
            "holdout_delta_vs_baseline": 9.0,
        },
    )

    X_val_full, y_val = _build_data()
    direct_model = _FakeDirectModel(
        np.full(len(X_val_full), 0.01024, dtype=float),
        profile="60min_12h",
        expectation=np.full(len(X_val_full), 0.14, dtype=float),
        confidence=np.full(len(X_val_full), 0.58, dtype=float),
    )

    strategy = ds._select_direct_strategy(
        direct_model,
        X_val_full,
        y_val,
        model_key="60min_12h",
    )

    assert strategy["prediction_stabilization"]["enabled"] is True
    assert strategy["prediction_stabilization_applied"] is True
    assert strategy["prediction_stabilization"]["model_key"] == "60min_12h"
    stats = dict(strategy.get("prediction_stabilization_stats", {}) or {})
    assert stats["mean_abs_deviation_after"] < stats["mean_abs_deviation_before"]
