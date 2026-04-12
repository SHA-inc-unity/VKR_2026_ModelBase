import numpy as np
import pandas as pd

from catboost_floader.models.direct import DirectModel
from catboost_floader.selection.direct_strategy import _calibrate_direction_thresholds


class _FakeDirectionModel:
    def __init__(self, probs: np.ndarray, class_signs: np.ndarray, expectation: np.ndarray):
        self._probs = np.asarray(probs, dtype=float)
        self._class_signs = np.asarray(class_signs, dtype=float)
        self._expectation = np.asarray(expectation, dtype=float)

    def predict_components(self, X: pd.DataFrame):
        n = len(X)
        probs = self._probs
        expectation = self._expectation
        if len(probs) != n:
            probs = np.resize(probs, (n, probs.shape[1]))
        if len(expectation) != n:
            expectation = np.resize(expectation, n)
        return {
            "probs": probs,
            "class_signs": self._class_signs,
            "expectation": expectation,
        }


class _FakeDirectModel:
    def __init__(self, probs: np.ndarray, class_signs: np.ndarray, expectation: np.ndarray):
        self.composition_profile = "main_direct_pipeline"
        self.composition_config = {"label_confidence_threshold": 0.9}
        self.direction_model = _FakeDirectionModel(probs, class_signs, expectation)


def test_direction_calibration_improves_metrics_and_avoids_single_class_collapse():
    class_signs = np.array([-1.0, 0.0, 1.0], dtype=float)
    probs = np.array(
        [
            [0.62, 0.18, 0.20],
            [0.58, 0.20, 0.22],
            [0.20, 0.22, 0.58],
            [0.18, 0.20, 0.62],
            [0.30, 0.40, 0.30],
            [0.28, 0.44, 0.28],
            [0.20, 0.25, 0.55],
            [0.56, 0.20, 0.24],
        ],
        dtype=float,
    )
    expectation = probs.dot(class_signs)
    direct_model = _FakeDirectModel(probs=probs, class_signs=class_signs, expectation=expectation)

    X_val = pd.DataFrame({"feat": np.arange(len(probs), dtype=float)})
    target_return = np.array([-0.0040, -0.0030, 0.0042, 0.0038, 0.0001, 0.0000, 0.0027, -0.0035], dtype=float)

    payload = _calibrate_direction_thresholds(
        direct_model=direct_model,
        X_model_aligned=X_val,
        target_return=target_return,
    )

    assert payload["applied"] is True
    assert payload["reason"] == "calibrated"
    assert payload["applies_to_profiles"] == ["main_direct_pipeline"]
    baseline_metrics = payload["baseline_metrics"]
    selected_metrics = payload["selected_metrics"]
    baseline_recent_metrics = payload["baseline_recent_metrics"]
    selected_recent_metrics = payload["selected_recent_metrics"]

    assert baseline_metrics["unique_predicted_classes"] == 1
    assert selected_metrics["unique_predicted_classes"] >= 2
    assert selected_metrics["neutral_overprediction"] <= payload["max_neutral_overprediction"]
    assert selected_metrics["neutral_recall"] >= payload["min_neutral_recall"]
    if selected_recent_metrics["neutral_true_rate"] > 0:
        assert selected_recent_metrics["neutral_recall"] >= payload["min_neutral_recall"]

    assert selected_metrics["macro_f1"] >= baseline_metrics["macro_f1"]
    assert selected_metrics["sign_accuracy"] >= baseline_metrics["sign_accuracy"]
    assert selected_metrics["neutral_f1"] >= baseline_metrics["neutral_f1"]
    assert selected_recent_metrics["macro_f1"] >= baseline_recent_metrics["macro_f1"]
    assert selected_recent_metrics["sign_accuracy"] >= baseline_recent_metrics["sign_accuracy"]
    assert selected_recent_metrics["neutral_f1"] >= baseline_recent_metrics["neutral_f1"]
    assert (
        selected_metrics["macro_f1"] > baseline_metrics["macro_f1"]
        or selected_metrics["sign_accuracy"] > baseline_metrics["sign_accuracy"]
    )

    # Persisted calibration remains the source of truth at runtime.
    runtime_model = DirectModel()
    runtime_model.direction_calibration = {
        "applied": True,
        "selected_confidence_threshold": payload["selected_confidence_threshold"],
        "selected_deadband": payload["selected_deadband"],
        "applies_to_profiles": payload["applies_to_profiles"],
    }
    resolved_threshold, resolved_deadband = runtime_model._resolve_direction_runtime_thresholds(
        cfg={"label_confidence_threshold": 0.99, "direction_deadband": 0.01},
        active_profile="main_direct_pipeline",
    )
    default_threshold, default_deadband = runtime_model._resolve_direction_runtime_thresholds(
        cfg={"label_confidence_threshold": 0.99, "direction_deadband": 0.01},
        active_profile="default",
    )
    assert np.isclose(resolved_threshold, float(payload["selected_confidence_threshold"]))
    assert np.isclose(resolved_deadband, float(payload["selected_deadband"]))
    assert np.isclose(default_threshold, 0.99)
    assert np.isclose(default_deadband, 0.01)
