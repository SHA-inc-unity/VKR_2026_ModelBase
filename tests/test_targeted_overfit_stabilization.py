import numpy as np
import pandas as pd

from catboost_floader.selection import direct_strategy as ds


class _FakeDirectModel:
    def __init__(self, raw_pred: np.ndarray, profile: str = "default"):
        self.feature_names = ["feat", "return_1"]
        self.composition_profile = profile
        self.composition_config = {"profile_enabled": True, "profile_fallbacks": []}
        self.direction_model = type("_DirectionStub", (), {"calibration": {}})()
        self._raw_pred = np.asarray(raw_pred, dtype=float)

    def predict_details(self, X, composition_profile=None, composition_config=None):
        n = len(X)
        raw = self._raw_pred
        if len(raw) != n:
            raw = np.resize(raw, n)
        return {
            "raw_pred_return": raw,
            "direction_label": np.sign(raw).astype(int),
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
