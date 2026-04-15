import numpy as np
import pandas as pd

from catboost_floader.selection import direct_strategy as ds


class _FakeDirectionModel:
    def __init__(self):
        self.calibration = {}

    def predict_components(self, X: pd.DataFrame):
        n = len(X)
        probs = np.tile(np.array([[0.2, 0.6, 0.2]], dtype=float), (n, 1))
        return {
            "probs": probs,
            "class_signs": np.array([-1.0, 0.0, 1.0], dtype=float),
            "expectation": np.zeros(n, dtype=float),
        }


class _FakeDirectModel:
    def __init__(self, raw_pred: np.ndarray, profile: str = "main_direct_pipeline"):
        self.feature_names = ["feat"]
        self.composition_profile = profile
        self.composition_config = {"profile_enabled": True, "profile_fallbacks": []}
        self.direction_model = _FakeDirectionModel()
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


def test_main_persistence_promotion_prefers_recent_delta(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_calibrate_direction_thresholds",
        lambda **kwargs: {
            "applied": False,
            "reason": "test_override",
            "applies_to_profiles": ["main_direct_pipeline"],
        },
    )
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: ["main_direct_pipeline", None])
    monkeypatch.setattr(ds, "MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_DELTA_VS_PERSISTENCE", 0.0)
    monkeypatch.setattr(ds, "MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_RECENT_DELTA_VS_PERSISTENCE", 0.0)
    monkeypatch.setattr(
        ds,
        "_direct_strategy_candidates",
        lambda _cfg: [
            {"type": "baseline_only", "alpha": 0.0, "baseline": "persistence"},
            {"type": "model_only", "alpha": 1.0, "baseline": "persistence"},
            {"type": "blend", "alpha": 0.8, "baseline": "persistence"},
        ],
    )
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

    # First 7 points: model-only is perfect.
    # Last 3 points: persistence is decent, model-only is flat and weaker.
    # Blend(0.8, persistence) is slightly worse overall MAE but has higher recent
    # delta vs persistence and should be promoted by the main-only gate.
    target_return = np.array([0.01] * 7 + [-0.01] * 3, dtype=float)
    raw_pred_return = np.array([0.01] * 7 + [0.0] * 3, dtype=float)
    persistence_return = np.array([0.0] * 7 + [-0.02] * 3, dtype=float)
    close = np.full(len(target_return), 100.0, dtype=float)

    X_val_full = pd.DataFrame(
        {
            "close": close,
            "feat": np.arange(len(target_return), dtype=float),
            "return_1": persistence_return,
            "ret_mean_6": np.zeros(len(target_return), dtype=float),
            "ret_mean_12": np.zeros(len(target_return), dtype=float),
        }
    )
    y_val = pd.DataFrame(
        {
            "target_return": target_return,
            "target_future_close": close * (1.0 + target_return),
        }
    )

    direct_model = _FakeDirectModel(raw_pred_return)
    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "blend"
    assert np.isclose(float(strategy["alpha"]), 0.8)

    promo = strategy.get("main_persistence_promotion", {})
    assert promo.get("applied") is True
    assert promo.get("reason") == "promoted_by_recent_delta_vs_persistence"
    assert promo.get("baseline_excluded_from_promotion") is True
    assert int(promo.get("promotable_non_baseline_candidates", 0)) == int(promo.get("promotable_candidates", 0))
    assert int(promo.get("promotable_non_baseline_candidates", 0)) > 0
    assert strategy.get("main_persistence_promotion_applied") is True
    assert int(strategy.get("main_persistence_promotable_non_baseline_count", 0)) == int(
        strategy.get("main_persistence_promotable_candidate_count", 0)
    )
    assert strategy.get("main_persistence_baseline_excluded_from_promotion") is True
    assert float(promo.get("selected_recent_delta_vs_persistence_after", 0.0)) > float(
        promo.get("selected_recent_delta_vs_persistence_before", 0.0)
    )


def test_main_baseline_fallback_remains_when_no_non_baseline_promotable(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_calibrate_direction_thresholds",
        lambda **kwargs: {
            "applied": False,
            "reason": "test_override",
            "applies_to_profiles": ["main_direct_pipeline"],
        },
    )
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: ["main_direct_pipeline", None])
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

    n = 12
    target_return = np.full(n, 0.01, dtype=float)
    raw_pred_return = np.full(n, -0.05, dtype=float)
    persistence_return = np.zeros(n, dtype=float)
    close = np.full(n, 100.0, dtype=float)

    X_val_full = pd.DataFrame(
        {
            "close": close,
            "feat": np.arange(n, dtype=float),
            "return_1": persistence_return,
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

    direct_model = _FakeDirectModel(raw_pred_return, profile="main_direct_pipeline")
    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "baseline_only"
    promo = strategy.get("main_persistence_promotion", {})
    assert promo.get("reason") in {"no_promotable_candidate", "best_already_promotable"}
    assert promo.get("baseline_excluded_from_promotion") is True
    assert int(promo.get("promotable_non_baseline_candidates", 0)) == 0
    assert strategy.get("main_persistence_promotion_applied") is False
    assert int(strategy.get("main_persistence_promotable_non_baseline_count", 0)) == 0
    assert strategy.get("main_persistence_baseline_excluded_from_promotion") is True


def test_non_main_persistence_promotion_path_unchanged(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_calibrate_direction_thresholds",
        lambda **kwargs: {
            "applied": False,
            "reason": "test_override",
            "applies_to_profiles": ["main_direct_pipeline"],
        },
    )
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: [None])
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

    n = 12
    target_return = np.full(n, 0.01, dtype=float)
    raw_pred_return = np.full(n, -0.05, dtype=float)
    persistence_return = np.zeros(n, dtype=float)
    close = np.full(n, 100.0, dtype=float)

    X_val_full = pd.DataFrame(
        {
            "close": close,
            "feat": np.arange(n, dtype=float),
            "return_1": persistence_return,
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

    direct_model = _FakeDirectModel(raw_pred_return, profile="default")
    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "baseline_only"
    promo = strategy.get("main_persistence_promotion", {})
    assert promo.get("reason") == "disabled_or_non_main"
    assert promo.get("baseline_excluded_from_promotion") is False
    assert strategy.get("main_persistence_promotion_applied") is False
    assert strategy.get("main_persistence_baseline_excluded_from_promotion") is False
