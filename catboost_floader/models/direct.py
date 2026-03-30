from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    DIRECT_CATBOOST_PARAMS,
    MODEL_DIR,
    MAGNITUDE_CALIBRATION,
    DIRECTION_DEADBAND,
    ANOMALY_MAGNITUDE_SHRINK,
    DIRECT_COMPOSITION_DEFAULTS,
    DIRECT_COMPOSITION_PROFILES,
)
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json
from catboost_floader.evaluation.backtest import build_direct_baselines
from catboost_floader.models.direction import DirectionModel
from catboost_floader.models.movement import MovementModel

logger = get_logger("model_direct")


def _resolve_composition_config(profile_name: Optional[str], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(DIRECT_COMPOSITION_DEFAULTS)
    if profile_name:
        cfg.update(DIRECT_COMPOSITION_PROFILES.get(profile_name, {}))
    if overrides:
        cfg.update(overrides)
    return cfg


class DirectModel:
    """Wrapper DirectModel that composes a DirectionModel and a MovementModel.

    Maintains backward compatibility: `predict(X)` returns a signed return (direction * magnitude).
    Saves/loads both underlying models as `prefix_direction` and `prefix_movement`.
    """

    def __init__(
        self,
        params: Optional[Dict[str, Any]] = None,
        direction_params: Optional[Dict[str, Any]] = None,
        strategy: Optional[Dict[str, Any]] = None,
        composition_profile: Optional[str] = None,
        composition_config: Optional[Dict[str, Any]] = None,
    ):
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
        self.composition_profile = composition_profile
        self.composition_config: Dict[str, Any] = _resolve_composition_config(composition_profile, composition_config)
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
        baseline = str(self.strategy.get("baseline", "persistence"))
        baselines = build_direct_baselines(X)
        baseline_cols = {
            "persistence": "baseline_persistence_return",
            "rolling_mean": "baseline_rolling_mean_return",
            "trend": "baseline_trend_return",
        }
        baseline_col = baseline_cols.get(baseline)
        if baseline_col is None or baseline_col not in baselines.columns:
            return np.zeros(len(X), dtype=float)
        return pd.to_numeric(baselines[baseline_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)

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

    def _fallback_direction_sign(self, X: pd.DataFrame) -> np.ndarray:
        """Heuristic sign when DirectionModel is effectively degenerate.

        Uses simple momentum features if доступны, иначе знак последнего ретёрна.
        """
        dead = float(DIRECTION_DEADBAND)
        X_num = X.select_dtypes(include=[np.number])
        if "ret_mean_6" in X_num.columns:
            base = pd.to_numeric(X_num["ret_mean_6"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        elif "return_1" in X_num.columns:
            base = pd.to_numeric(X_num["return_1"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        else:
            return np.zeros(len(X_num), dtype=float)
        signs = np.sign(base)
        signs[np.abs(base) < dead] = 0.0
        return signs.astype(float)

    def _expectation_sign(self, expectation: np.ndarray) -> np.ndarray:
        cfg = self.composition_config
        out = np.clip(np.asarray(expectation, dtype=float), -1.0, 1.0)
        power = float(cfg.get("expectation_power", 1.0))
        if power != 1.0:
            out = np.sign(out) * (np.abs(out) ** power)
        deadband = float(cfg.get("expectation_deadband", 0.0))
        if deadband > 0:
            out[np.abs(out) < deadband] = 0.0
        return out

    def _compose_direction_signal(self, X: pd.DataFrame) -> Dict[str, np.ndarray]:
        if getattr(self.direction_model, "is_degenerate", False):
            fallback = self._fallback_direction_sign(X)
            nan_probs = np.full((len(fallback), 3), np.nan, dtype=float)
            return {
                "sign": fallback.astype(float),
                "label": fallback.astype(int),
                "expectation": fallback.astype(float),
                "probs": nan_probs,
            }

        try:
            components = self.direction_model.predict_components(X)
            probs = np.asarray(components["probs"], dtype=float)
            max_p = np.nanmax(probs, axis=1)
            labels = np.asarray(components["label"], dtype=float)
            expectation = self._expectation_sign(components["expectation"])
            threshold = float(self.composition_config.get("label_confidence_threshold", 0.0))
            low_conf_mode = str(self.composition_config.get("low_confidence_sign_mode", "neutral"))
            if low_conf_mode == "expectation":
                sign = np.where(max_p >= threshold, labels, expectation)
            else:
                sign = np.where(max_p >= threshold, labels, 0.0)
            return {
                "sign": np.clip(np.asarray(sign, dtype=float), -1.0, 1.0),
                "label": np.asarray(components["label"], dtype=int),
                "expectation": np.asarray(components["expectation"], dtype=float),
                "probs": probs,
            }
        except Exception:
            expectation = self._expectation_sign(self.direction_model.predict_sign_expectation(X))
            nan_probs = np.full((len(expectation), 3), np.nan, dtype=float)
            return {
                "sign": expectation.astype(float),
                "label": np.sign(expectation).astype(int),
                "expectation": expectation.astype(float),
                "probs": nan_probs,
            }

    def predict_details(self, X: pd.DataFrame) -> Dict[str, np.ndarray]:
        X_orig = X.copy()
        movement_magnitude = np.asarray(self.movement_model.predict(X_orig), dtype=float)
        direction = self._compose_direction_signal(X_orig)
        sign = np.asarray(direction["sign"], dtype=float)

        adjusted_magnitude = movement_magnitude.copy()
        try:
            anomaly_score = pd.to_numeric(X_orig.get("anomaly_score", 0.0), errors="coerce").fillna(0.0).to_numpy(dtype=float)
            shrink = float(ANOMALY_MAGNITUDE_SHRINK)
            scale = 1.0 - shrink * np.clip(anomaly_score, 0.0, 1.0)
            scale = np.maximum(scale, 0.2)
            adjusted_magnitude = adjusted_magnitude * scale
        except Exception:
            pass
        raw = np.asarray(sign * adjusted_magnitude, dtype=float)
        try:
            calib = float(MAGNITUDE_CALIBRATION)
        except Exception:
            calib = 1.0
        if calib != 1.0:
            raw = raw * calib
        pred_return = self._apply_strategy(raw, X_orig)
        return {
            "pred_return": np.asarray(pred_return, dtype=float),
            "raw_pred_return": raw,
            "movement_pred_magnitude": movement_magnitude,
            "direction_sign": sign,
            "direction_label": np.asarray(direction["label"], dtype=int),
            "direction_expectation": np.asarray(direction["expectation"], dtype=float),
            "direction_proba": np.asarray(direction["probs"], dtype=float),
        }

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.predict_details(X)["pred_return"]

    def save(self, prefix: str):
        ensure_dirs([os.path.dirname(prefix)])
        # save submodels
        self.direction_model.save(prefix)
        self.movement_model.save(prefix)
        save_json(
            {
                "feature_names": self.feature_names,
                "strategy": self.strategy,
                "magnitude_calibration": MAGNITUDE_CALIBRATION,
                "composition_profile": self.composition_profile,
                "composition_config": self.composition_config,
            },
            prefix + ".json",
        )

    def load(self, prefix: str):
        self.direction_model.load(prefix)
        self.movement_model.load(prefix)
        meta = load_json(prefix + ".json") or {}
        self.feature_names = meta.get("feature_names", self.feature_names)
        self.strategy = meta.get("strategy", self.strategy)
        self.composition_profile = meta.get("composition_profile", self.composition_profile)
        self.composition_config = _resolve_composition_config(self.composition_profile, meta.get("composition_config"))
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
    composition_profile: Optional[str] = None,
    composition_config: Optional[Dict[str, Any]] = None,
    save: bool = True,
) -> DirectModel:
    model = DirectModel(
        params=params,
        strategy=strategy,
        composition_profile=composition_profile,
        composition_config=composition_config,
    )
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
