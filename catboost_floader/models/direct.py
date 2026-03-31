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


def _resolve_profile_settings(profile_name: Optional[str], seen: Optional[set[str]] = None) -> Dict[str, Any]:
    if not profile_name or profile_name == "default":
        return {}
    if seen is None:
        seen = set()
    if profile_name in seen:
        logger.warning("Detected recursive direct composition profile inheritance for %s", profile_name)
        return {}
    seen.add(profile_name)
    profile_cfg = dict(DIRECT_COMPOSITION_PROFILES.get(profile_name, {}) or {})
    parent_name = profile_cfg.pop("inherits", None)
    resolved = _resolve_profile_settings(parent_name, seen)
    resolved.update(profile_cfg)
    return resolved


def _resolve_composition_config(profile_name: Optional[str], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(DIRECT_COMPOSITION_DEFAULTS)
    cfg.update(_resolve_profile_settings(profile_name))
    if overrides:
        cfg.update(overrides)
    return cfg


def resolve_direct_composition_config(profile_name: Optional[str], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _resolve_composition_config(profile_name, overrides)


def _weighted_direction_mix(
    label_signal: np.ndarray,
    expectation_signal: np.ndarray,
    *,
    label_weight: float,
    expectation_weight: float,
) -> np.ndarray:
    label_arr = np.asarray(label_signal, dtype=float)
    expectation_arr = np.asarray(expectation_signal, dtype=float)
    total_weight = abs(label_weight) + abs(expectation_weight)
    if total_weight <= 0:
        return expectation_arr.copy()
    mixed = (label_weight * label_arr + expectation_weight * expectation_arr) / total_weight
    return np.clip(np.asarray(mixed, dtype=float), -1.0, 1.0)


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
        self._magnitude_calibration = float(MAGNITUDE_CALIBRATION)
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

    def _compose_sign_mode(
        self,
        mode: str,
        label_signal: np.ndarray,
        expectation_signal: np.ndarray,
        *,
        prefix: str,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> np.ndarray:
        if cfg is None:
            cfg = self.composition_config
        mode_name = str(mode or "").lower()
        label_arr = np.clip(np.asarray(label_signal, dtype=float), -1.0, 1.0)
        expectation_arr = np.clip(np.asarray(expectation_signal, dtype=float), -1.0, 1.0)
        if mode_name == "neutral":
            return np.zeros_like(expectation_arr, dtype=float)
        if mode_name == "expectation":
            return expectation_arr
        if mode_name == "blend":
            label_weight = float(cfg.get(f"{prefix}_label_weight", 0.0))
            expectation_weight = float(cfg.get(f"{prefix}_expectation_weight", 1.0))
            return _weighted_direction_mix(
                label_arr,
                expectation_arr,
                label_weight=label_weight,
                expectation_weight=expectation_weight,
            )
        return label_arr

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

    def _expectation_sign(self, expectation: np.ndarray, cfg: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if cfg is None:
            cfg = self.composition_config
        out = np.clip(np.asarray(expectation, dtype=float), -1.0, 1.0)
        power = float(cfg.get("expectation_power", 1.0))
        if power != 1.0:
            out = np.sign(out) * (np.abs(out) ** power)
        deadband = float(cfg.get("expectation_deadband", 0.0))
        if deadband > 0:
            out[np.abs(out) < deadband] = 0.0
        return out

    def _scale_movement_magnitude(
        self,
        X: pd.DataFrame,
        movement_magnitude: np.ndarray,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> np.ndarray:
        if cfg is None:
            cfg = self.composition_config
        adjusted = np.asarray(movement_magnitude, dtype=float).copy()
        anomaly_floor = float(cfg.get("anomaly_magnitude_floor", 0.2))
        try:
            anomaly_score = pd.to_numeric(X.get("anomaly_score", 0.0), errors="coerce").fillna(0.0).to_numpy(dtype=float)
            shrink = float(ANOMALY_MAGNITUDE_SHRINK)
            scale = 1.0 - shrink * np.clip(anomaly_score, 0.0, 1.0)
            scale = np.maximum(scale, anomaly_floor)
            adjusted = adjusted * scale
        except Exception:
            pass

        movement_scale = float(cfg.get("movement_scale", 1.0))
        calib = float(getattr(self, "_magnitude_calibration", MAGNITUDE_CALIBRATION))
        total_scale = movement_scale * calib
        if total_scale != 1.0:
            adjusted = adjusted * total_scale
        return adjusted

    def _compose_direction_signal(self, X: pd.DataFrame, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, np.ndarray]:
        if cfg is None:
            cfg = self.composition_config
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
            if probs.ndim == 2 and probs.shape[1] > 0:
                max_p = np.nanmax(probs, axis=1)
            else:
                max_p = np.full(len(X), np.nan, dtype=float)
            labels = np.asarray(components["label"], dtype=float)
            expectation = self._expectation_sign(components["expectation"], cfg=cfg)
            threshold = float(cfg.get("label_confidence_threshold", 0.0))
            high_conf_mode = str(cfg.get("high_confidence_sign_mode", "label"))
            low_conf_mode = str(cfg.get("low_confidence_sign_mode", "neutral"))
            high_conf_sign = self._compose_sign_mode(high_conf_mode, labels, expectation, prefix="high_confidence", cfg=cfg)
            low_conf_sign = self._compose_sign_mode(low_conf_mode, labels, expectation, prefix="low_confidence", cfg=cfg)
            sign = np.where(max_p >= threshold, high_conf_sign, low_conf_sign)
            return {
                "sign": np.clip(np.asarray(sign, dtype=float), -1.0, 1.0),
                "label": np.asarray(components["label"], dtype=int),
                "expectation": np.asarray(components["expectation"], dtype=float),
                "probs": probs,
            }
        except Exception:
            expectation = self._expectation_sign(self.direction_model.predict_sign_expectation(X), cfg=cfg)
            nan_probs = np.full((len(expectation), 3), np.nan, dtype=float)
            return {
                "sign": expectation.astype(float),
                "label": np.sign(expectation).astype(int),
                "expectation": expectation.astype(float),
                "probs": nan_probs,
            }

    def predict_details(
        self,
        X: pd.DataFrame,
        *,
        composition_profile: Optional[str] = None,
        composition_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, np.ndarray]:
        X_orig = X.copy()
        active_profile = self.composition_profile if composition_profile is None else composition_profile
        active_cfg = (
            self.composition_config
            if composition_profile is None and composition_config is None
            else _resolve_composition_config(active_profile, composition_config)
        )
        movement_magnitude = np.asarray(self.movement_model.predict(X_orig), dtype=float)
        direction = self._compose_direction_signal(X_orig, cfg=active_cfg)
        sign = np.asarray(direction["sign"], dtype=float)
        adjusted_magnitude = self._scale_movement_magnitude(X_orig, movement_magnitude, cfg=active_cfg)
        raw = np.asarray(sign * adjusted_magnitude, dtype=float)
        pred_return = self._apply_strategy(raw, X_orig)
        return {
            "pred_return": np.asarray(pred_return, dtype=float),
            "raw_pred_return": raw,
            "movement_pred_magnitude": movement_magnitude,
            "movement_scaled_magnitude": np.asarray(adjusted_magnitude, dtype=float),
            "direction_sign": sign,
            "direction_label": np.asarray(direction["label"], dtype=int),
            "direction_expectation": np.asarray(direction["expectation"], dtype=float),
            "direction_proba": np.asarray(direction["probs"], dtype=float),
        }

    def predict(
        self,
        X: pd.DataFrame,
        *,
        composition_profile: Optional[str] = None,
        composition_config: Optional[Dict[str, Any]] = None,
    ) -> np.ndarray:
        return self.predict_details(
            X,
            composition_profile=composition_profile,
            composition_config=composition_config,
        )["pred_return"]

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
