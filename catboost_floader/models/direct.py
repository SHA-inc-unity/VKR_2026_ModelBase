from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from catboost_floader.core.config import DIRECT_CATBOOST_PARAMS, MODEL_DIR, MAGNITUDE_CALIBRATION
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json
from catboost_floader.models.direction import DirectionModel
from catboost_floader.models.movement import MovementModel

logger = get_logger("model_direct")


class DirectModel:
    """Wrapper DirectModel that composes a DirectionModel and a MovementModel.

    Maintains backward compatibility: `predict(X)` returns a signed return (direction * magnitude).
    Saves/loads both underlying models as `prefix_direction` and `prefix_movement`.
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None, direction_params: Optional[Dict[str, Any]] = None, strategy: Optional[Dict[str, Any]] = None):
        self.movement_model = MovementModel(params=params or DIRECT_CATBOOST_PARAMS.copy())
        # Derive direction params if not provided
        if direction_params is None:
            # Start from regressor params but switch to classification loss/metric
            direction_params = (params or DIRECT_CATBOOST_PARAMS.copy()).copy()
            direction_params["loss_function"] = "Logloss"
            direction_params["eval_metric"] = "AUC"
        self.direction_model = DirectionModel(params=direction_params)
        self.feature_names: list[str] = []
        self.strategy: Dict[str, Any] = strategy or {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}
        # backward-compatible single-model reference (uses movement model for feature importance)
        self.model = getattr(self.movement_model, "model", None)

    def fit(self, X: pd.DataFrame, y: pd.Series):
        # X: features, y: series of target returns
        self.feature_names = list(X.columns)
        self.direction_model.fit(X, y)
        self.movement_model.fit(X, y)
        # update proxy
        self.model = getattr(self.movement_model, "model", None)
        logger.info("Trained DirectModel (direction + movement)")
        return self

    def _baseline_return(self, X: pd.DataFrame) -> np.ndarray:
        # preserved from previous implementation; kept local here to avoid circular imports
        baseline = str(self.strategy.get("baseline", "persistence"))
        if baseline == "rolling_mean":
            if "ret_mean_30" in X.columns:
                return pd.to_numeric(X["ret_mean_30"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
            return np.zeros(len(X), dtype=float)
        if baseline == "trend":
            if "ret_mean_60" in X.columns:
                return pd.to_numeric(X["ret_mean_60"], errors="coerce").fillna(0.0).to_numpy(dtype=float) * 3.0
            return np.zeros(len(X), dtype=float)
        return np.zeros(len(X), dtype=float)

    def _apply_strategy(self, raw_pred: np.ndarray, X_original: pd.DataFrame) -> np.ndarray:
        raw_pred = np.asarray(raw_pred, dtype=float)
        strategy_type = str(self.strategy.get("type", "model_only"))
        alpha = float(self.strategy.get("alpha", 1.0))

        if strategy_type == "baseline_only":
            return self._baseline_return(X_original)
        if strategy_type == "blend":
            baseline = self._baseline_return(X_original)
            return alpha * raw_pred + (1.0 - alpha) * baseline
        return raw_pred

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        # Combined prediction: expected sign * predicted magnitude
        X_orig = X.copy()
        # movement magnitude
        mag = self.movement_model.predict(X_orig)
        # expected sign in [-1,1]
        sign = self.direction_model.predict_sign_expectation(X_orig)
        raw = np.asarray(sign * mag, dtype=float)
        # Apply optional global calibration for magnitude (quick experimental knob)
        try:
            calib = float(MAGNITUDE_CALIBRATION)
        except Exception:
            calib = 1.0
        if calib != 1.0:
            raw = raw * calib
        return self._apply_strategy(raw, X_orig)

    def save(self, prefix: str):
        ensure_dirs([os.path.dirname(prefix)])
        # save submodels
        self.direction_model.save(prefix)
        self.movement_model.save(prefix)
        save_json({"feature_names": self.feature_names, "strategy": self.strategy, "magnitude_calibration": MAGNITUDE_CALIBRATION}, prefix + ".json")

    def load(self, prefix: str):
        self.direction_model.load(prefix)
        self.movement_model.load(prefix)
        meta = load_json(prefix + ".json") or {}
        self.feature_names = meta.get("feature_names", self.feature_names)
        self.strategy = meta.get("strategy", self.strategy)
        # if a calibration factor was saved with the model metadata, prefer it
        if isinstance(meta.get("magnitude_calibration"), (int, float)):
            try:
                # override config-level default for this model instance
                self._magnitude_calibration = float(meta.get("magnitude_calibration"))
            except Exception:
                self._magnitude_calibration = MAGNITUDE_CALIBRATION
        else:
            self._magnitude_calibration = MAGNITUDE_CALIBRATION
        self.model = getattr(self.movement_model, "model", None)
        return self


def train_direct_model(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    params: Optional[Dict[str, Any]] = None,
    strategy: Optional[Dict[str, Any]] = None,
    save: bool = True,
) -> DirectModel:
    model = DirectModel(params=params, strategy=strategy)
    # targets can be a DataFrame with 'target_return' column, or a Series
    if isinstance(targets, pd.DataFrame) and "target_return" in targets.columns:
        y = targets["target_return"]
    else:
        y = targets
    model.fit(features, y)
    if save:
        ensure_dirs([MODEL_DIR])
        model.save(os.path.join(MODEL_DIR, "direct_model"))
    return model
