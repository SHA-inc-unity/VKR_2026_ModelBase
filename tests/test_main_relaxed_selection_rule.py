import numpy as np
import pandas as pd

from catboost_floader.selection import direct_strategy as ds


class _FakeDirectionModel:
    def __init__(self):
        self.calibration = {}


class _FakeDirectModel:
    def __init__(self, raw_pred: np.ndarray, profile: str):
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


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        ds,
        "_calibrate_direction_thresholds",
        lambda **kwargs: {
            "applied": False,
            "reason": "test_override",
            "applies_to_profiles": ["main_direct_pipeline"],
        },
    )
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


def _build_validation_data(n: int = 12):
    # Persistence is decent and acts as baseline_only candidate.
    # Model is slightly worse (around -0.3% vs persistence) and should be allowed
    # for main profile by relaxed negative-delta selection tolerance.
    target_return = np.full(n, 0.01, dtype=float)
    persistence_return = np.zeros(n, dtype=float)
    raw_pred_return = np.full(n, -0.00003, dtype=float)
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
    return raw_pred_return, X_val_full, y_val


def test_main_profile_relaxed_rule_promotes_non_baseline(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: ["main_direct_pipeline", None])

    raw_pred_return, X_val_full, y_val = _build_validation_data()
    direct_model = _FakeDirectModel(raw_pred_return, profile="main_direct_pipeline")

    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "model_only"
    assert strategy.get("composition_profile") == "main_direct_pipeline"
    assert strategy.get("main_selection_relaxed_rule_applied") is True
    relaxed = strategy.get("main_selection_relaxed_rule", {})
    assert relaxed.get("applied") is True
    assert str(relaxed.get("reason", "")).startswith("promoted_main_non_baseline")


def test_default_profile_does_not_use_main_relaxed_rule(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: [None])

    raw_pred_return, X_val_full, y_val = _build_validation_data()
    direct_model = _FakeDirectModel(raw_pred_return, profile="default")

    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "baseline_only"
    assert strategy.get("main_selection_relaxed_rule_applied") is False


def test_main_near_tie_overrides_default_baseline(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(ds, "_direct_profile_sequence", lambda _model: [None, "main_direct_pipeline"])

    raw_pred_return, X_val_full, y_val = _build_validation_data()
    direct_model = _FakeDirectModel(raw_pred_return, profile="main_direct_pipeline")

    strategy = ds._select_direct_strategy(direct_model, X_val_full, y_val)

    assert strategy["type"] == "model_only"
    assert strategy.get("composition_profile") == "main_direct_pipeline"
    assert strategy.get("main_selection_baseline_overridden") is True
    assert strategy.get("main_selection_candidate_type") == "model_only"
    assert strategy.get("main_selection_final_ranking_reason") in {
        "near_tie_prefer_non_baseline_main",
        "main_relaxed_non_baseline_promotion",
    }
