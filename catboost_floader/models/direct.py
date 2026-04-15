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
    OVERFIT_STABILIZATION_PREDICTION_BASELINE_MEAN_ABS_WEIGHT,
    OVERFIT_STABILIZATION_PREDICTION_CONFIDENCE_THRESHOLD_BUFFER,
    OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_FLOOR,
    OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_MULTIPLIER,
    OVERFIT_STABILIZATION_PREDICTION_LOW_CONFIDENCE_SHRINK_MAX,
    OVERFIT_STABILIZATION_PREDICTION_SIGNAL_CONFIDENCE_WEIGHT,
    OVERFIT_STABILIZATION_PREDICTION_SIGNAL_EXPECTATION_WEIGHT,
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


def _strategy_aggressiveness(strategy: Optional[Dict[str, Any]]) -> float:
    strategy_payload = dict(strategy or {})
    strategy_type = str(strategy_payload.get("type", "model_only"))
    if strategy_type == "baseline_only":
        return 0.0
    if strategy_type == "blend":
        return max(0.0, min(1.0, float(strategy_payload.get("alpha", 0.0))))
    return 1.0


def _soft_limit(values: np.ndarray, limit: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    safe_limit = np.maximum(np.asarray(limit, dtype=float), 1e-8)
    return np.sign(arr) * safe_limit * np.tanh(arr / safe_limit)


def apply_direct_prediction_stabilization(
    candidate_return: np.ndarray,
    baseline_return: np.ndarray,
    *,
    direction_expectation: Optional[np.ndarray] = None,
    direction_proba: Optional[np.ndarray] = None,
    direction_confidence_threshold: Optional[float] = None,
    strategy: Optional[Dict[str, Any]] = None,
) -> tuple[np.ndarray, Dict[str, Any]]:
    strategy_payload = dict(strategy or {})
    policy = dict(strategy_payload.get("prediction_stabilization", {}) or {})
    candidate_arr = np.asarray(candidate_return, dtype=float)
    baseline_arr = np.asarray(baseline_return, dtype=float)
    if len(baseline_arr) != len(candidate_arr):
        baseline_arr = np.resize(baseline_arr, len(candidate_arr))

    summary: Dict[str, Any] = {
        "applied": False,
        "reason": "inactive",
        "mean_abs_return_before": float(np.mean(np.abs(candidate_arr))) if len(candidate_arr) else 0.0,
        "mean_abs_return_after": float(np.mean(np.abs(candidate_arr))) if len(candidate_arr) else 0.0,
        "mean_abs_deviation_before": float(np.mean(np.abs(candidate_arr - baseline_arr))) if len(candidate_arr) else 0.0,
        "mean_abs_deviation_after_confidence": float(np.mean(np.abs(candidate_arr - baseline_arr))) if len(candidate_arr) else 0.0,
        "mean_abs_deviation_after": float(np.mean(np.abs(candidate_arr - baseline_arr))) if len(candidate_arr) else 0.0,
        "mean_signal_strength": None,
        "mean_shrink_factor": 1.0,
        "deviation_soft_limit": None,
        "strategy_aggressiveness": float(_strategy_aggressiveness(strategy_payload)),
    }
    if not bool(policy.get("enabled", False)):
        return candidate_arr, summary

    deviation_before = candidate_arr - baseline_arr
    if not len(candidate_arr):
        return candidate_arr, summary
    if not np.any(np.abs(deviation_before) > 1e-12):
        summary["reason"] = "no_deviation"
        return candidate_arr, summary

    expectation_arr = np.asarray(
        direction_expectation if direction_expectation is not None else np.zeros(len(candidate_arr), dtype=float),
        dtype=float,
    )
    if len(expectation_arr) != len(candidate_arr):
        expectation_arr = np.resize(expectation_arr, len(candidate_arr))
    expectation_strength = np.clip(np.abs(expectation_arr), 0.0, 1.0)

    probs_arr = np.asarray(direction_proba, dtype=float) if direction_proba is not None else np.full((len(candidate_arr), 0), np.nan)
    if probs_arr.ndim == 2 and probs_arr.shape[0] == len(candidate_arr) and probs_arr.shape[1] > 0:
        confidence_arr = np.nanmax(probs_arr, axis=1)
    else:
        confidence_arr = np.full(len(candidate_arr), np.nan, dtype=float)

    confidence_threshold = float(direction_confidence_threshold or 0.0)
    confidence_buffer = max(
        0.0,
        float(policy.get("confidence_threshold_buffer", OVERFIT_STABILIZATION_PREDICTION_CONFIDENCE_THRESHOLD_BUFFER)),
    )
    confidence_floor = max(0.0, confidence_threshold - confidence_buffer)
    confidence_span = max(1e-8, 1.0 - confidence_floor)
    confidence_strength = np.clip(
        (np.nan_to_num(confidence_arr, nan=confidence_floor) - confidence_floor) / confidence_span,
        0.0,
        1.0,
    )
    confidence_strength = np.where(np.isnan(confidence_arr), expectation_strength, confidence_strength)

    confidence_weight = float(
        policy.get("signal_confidence_weight", OVERFIT_STABILIZATION_PREDICTION_SIGNAL_CONFIDENCE_WEIGHT)
    )
    expectation_weight = float(
        policy.get("signal_expectation_weight", OVERFIT_STABILIZATION_PREDICTION_SIGNAL_EXPECTATION_WEIGHT)
    )
    total_weight = max(abs(confidence_weight) + abs(expectation_weight), 1e-8)
    signal_strength = np.clip(
        (confidence_weight * confidence_strength + expectation_weight * expectation_strength) / total_weight,
        0.0,
        1.0,
    )

    aggressiveness = float(_strategy_aggressiveness(strategy_payload))
    max_shrink = np.clip(
        float(policy.get("low_confidence_shrink_max", OVERFIT_STABILIZATION_PREDICTION_LOW_CONFIDENCE_SHRINK_MAX)),
        0.0,
        0.95,
    )
    shrink_scale = 1.0 - max_shrink * aggressiveness * (1.0 - signal_strength)
    shrink_floor = max(0.05, 1.0 - max_shrink)
    shrink_scale = np.clip(shrink_scale, shrink_floor, 1.0)
    deviation_after_confidence = deviation_before * shrink_scale
    summary["mean_abs_deviation_after_confidence"] = float(np.mean(np.abs(deviation_after_confidence)))

    baseline_mean_abs_weight = max(
        0.0,
        float(policy.get("baseline_mean_abs_weight", OVERFIT_STABILIZATION_PREDICTION_BASELINE_MEAN_ABS_WEIGHT)),
    )
    deviation_soft_limit_floor = max(
        0.0,
        float(policy.get("deviation_soft_limit_floor", OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_FLOOR)),
    )
    deviation_soft_limit_multiplier = max(
        0.05,
        float(
            policy.get(
                "deviation_soft_limit_multiplier",
                OVERFIT_STABILIZATION_PREDICTION_DEVIATION_SOFT_LIMIT_MULTIPLIER,
            )
        ),
    )
    deviation_reference = max(
        float(np.mean(np.abs(deviation_before))),
        float(np.std(deviation_before)),
        float(np.mean(np.abs(deviation_after_confidence))),
        float(np.std(deviation_after_confidence)),
        float(np.mean(np.abs(baseline_arr)) * baseline_mean_abs_weight),
    )
    deviation_soft_limit_base = max(
        deviation_soft_limit_floor,
        deviation_reference * deviation_soft_limit_multiplier,
    )
    deviation_soft_limit = np.maximum(
        deviation_soft_limit_base * (1.0 + 0.35 * signal_strength + 0.15 * (1.0 - aggressiveness)),
        deviation_soft_limit_floor,
    )
    deviation_after_limit = _soft_limit(deviation_after_confidence, deviation_soft_limit)
    stabilized_return = baseline_arr + deviation_after_limit

    summary.update(
        {
            "applied": True,
            "reason": "targeted_prediction_stabilization",
            "mean_abs_return_after": float(np.mean(np.abs(stabilized_return))),
            "mean_abs_deviation_after": float(np.mean(np.abs(deviation_after_limit))),
            "mean_signal_strength": float(np.mean(signal_strength)),
            "mean_shrink_factor": float(
                np.mean(
                    np.divide(
                        np.abs(deviation_after_limit),
                        np.maximum(np.abs(deviation_before), 1e-8),
                    )
                )
            ),
            "deviation_soft_limit": float(np.mean(deviation_soft_limit)),
        }
    )
    return stabilized_return, summary


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
        self.direction_calibration: Dict[str, Any] = {}
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

    def _apply_strategy(
        self,
        raw_pred: np.ndarray,
        X_original: pd.DataFrame,
        *,
        direction_expectation: Optional[np.ndarray] = None,
        direction_proba: Optional[np.ndarray] = None,
        direction_confidence_threshold: Optional[float] = None,
    ) -> tuple[np.ndarray, Dict[str, Any]]:
        raw_pred = np.asarray(raw_pred, dtype=float)
        strategy_type = str(self.strategy.get("type", "model_only"))
        alpha = float(self.strategy.get("alpha", 1.0))
        baseline = self._baseline_return(X_original)

        if strategy_type == "baseline_only":
            candidate_return = baseline
        elif strategy_type == "blend":
            candidate_return = alpha * raw_pred + (1.0 - alpha) * baseline
        else:
            candidate_return = raw_pred

        stabilized_return, stabilization_summary = apply_direct_prediction_stabilization(
            candidate_return,
            baseline,
            direction_expectation=direction_expectation,
            direction_proba=direction_proba,
            direction_confidence_threshold=direction_confidence_threshold,
            strategy=self.strategy,
        )
        return stabilized_return, stabilization_summary

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

    def _resolve_direction_runtime_thresholds(
        self,
        *,
        cfg: Dict[str, Any],
        active_profile: Optional[str],
    ) -> tuple[float, float]:
        confidence_threshold = float(cfg.get("label_confidence_threshold", 0.0))
        direction_deadband = max(0.0, float(cfg.get("direction_deadband", DIRECTION_DEADBAND)))

        calibration = dict(getattr(self, "direction_calibration", {}) or {})
        if not bool(calibration.get("applied", False)):
            return confidence_threshold, direction_deadband

        applies_to_profiles = calibration.get("applies_to_profiles")
        profile_key = "default" if active_profile in (None, "", "default") else str(active_profile)
        if isinstance(applies_to_profiles, list) and profile_key not in {str(p) for p in applies_to_profiles}:
            return confidence_threshold, direction_deadband

        calibrated_threshold = calibration.get("selected_confidence_threshold")
        calibrated_deadband = calibration.get("selected_deadband")
        if calibrated_threshold is not None:
            try:
                confidence_threshold = float(calibrated_threshold)
            except Exception:
                pass
        if calibrated_deadband is not None:
            try:
                direction_deadband = max(0.0, float(calibrated_deadband))
            except Exception:
                pass
        return confidence_threshold, direction_deadband

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

    def _compose_direction_signal(
        self,
        X: pd.DataFrame,
        cfg: Optional[Dict[str, Any]] = None,
        *,
        active_profile: Optional[str] = None,
    ) -> Dict[str, np.ndarray]:
        if cfg is None:
            cfg = self.composition_config
        if getattr(self.direction_model, "is_degenerate", False):
            fallback = self._fallback_direction_sign(X)
            nan_probs = np.full((len(fallback), 3), np.nan, dtype=float)
            confidence_threshold, direction_deadband = self._resolve_direction_runtime_thresholds(
                cfg=cfg,
                active_profile=active_profile,
            )
            return {
                "sign": fallback.astype(float),
                "label": fallback.astype(int),
                "expectation": fallback.astype(float),
                "probs": nan_probs,
                "confidence_threshold": float(confidence_threshold),
                "direction_deadband": float(direction_deadband),
            }

        try:
            components = self.direction_model.predict_components(X)
            probs = np.asarray(components["probs"], dtype=float)
            if probs.ndim == 2 and probs.shape[1] > 0:
                max_p = np.nanmax(probs, axis=1)
            else:
                max_p = np.full(len(X), np.nan, dtype=float)
            labels = np.asarray(components["label"], dtype=float)
            raw_expectation = np.asarray(components["expectation"], dtype=float)
            threshold, direction_deadband = self._resolve_direction_runtime_thresholds(
                cfg=cfg,
                active_profile=active_profile,
            )
            expectation = self._expectation_sign(raw_expectation, cfg=cfg)
            if direction_deadband > 0.0:
                labels[np.abs(raw_expectation) < direction_deadband] = 0.0
            high_conf_mode = str(cfg.get("high_confidence_sign_mode", "label"))
            low_conf_mode = str(cfg.get("low_confidence_sign_mode", "neutral"))
            high_conf_sign = self._compose_sign_mode(high_conf_mode, labels, expectation, prefix="high_confidence", cfg=cfg)
            low_conf_sign = self._compose_sign_mode(low_conf_mode, labels, expectation, prefix="low_confidence", cfg=cfg)
            sign = np.where(max_p >= threshold, high_conf_sign, low_conf_sign)
            return {
                "sign": np.clip(np.asarray(sign, dtype=float), -1.0, 1.0),
                "label": np.asarray(labels, dtype=int),
                "expectation": np.asarray(expectation, dtype=float),
                "probs": probs,
                "confidence_threshold": float(threshold),
                "direction_deadband": float(direction_deadband),
            }
        except Exception:
            expectation = self._expectation_sign(self.direction_model.predict_sign_expectation(X), cfg=cfg)
            nan_probs = np.full((len(expectation), 3), np.nan, dtype=float)
            confidence_threshold, direction_deadband = self._resolve_direction_runtime_thresholds(
                cfg=cfg,
                active_profile=active_profile,
            )
            return {
                "sign": expectation.astype(float),
                "label": np.sign(expectation).astype(int),
                "expectation": expectation.astype(float),
                "probs": nan_probs,
                "confidence_threshold": float(confidence_threshold),
                "direction_deadband": float(direction_deadband),
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
        direction = self._compose_direction_signal(X_orig, cfg=active_cfg, active_profile=active_profile)
        sign = np.asarray(direction["sign"], dtype=float)
        adjusted_magnitude = self._scale_movement_magnitude(X_orig, movement_magnitude, cfg=active_cfg)
        raw = np.asarray(sign * adjusted_magnitude, dtype=float)
        pred_return, prediction_stabilization = self._apply_strategy(
            raw,
            X_orig,
            direction_expectation=np.asarray(direction["expectation"], dtype=float),
            direction_proba=np.asarray(direction["probs"], dtype=float),
            direction_confidence_threshold=float(direction.get("confidence_threshold", active_cfg.get("label_confidence_threshold", 0.0))),
        )
        return {
            "pred_return": np.asarray(pred_return, dtype=float),
            "raw_pred_return": raw,
            "movement_pred_magnitude": movement_magnitude,
            "movement_scaled_magnitude": np.asarray(adjusted_magnitude, dtype=float),
            "direction_sign": sign,
            "direction_label": np.asarray(direction["label"], dtype=int),
            "direction_expectation": np.asarray(direction["expectation"], dtype=float),
            "direction_proba": np.asarray(direction["probs"], dtype=float),
            "direction_confidence_threshold": float(direction.get("confidence_threshold", active_cfg.get("label_confidence_threshold", 0.0))),
            "direction_deadband": float(direction.get("direction_deadband", active_cfg.get("direction_deadband", DIRECTION_DEADBAND))),
            "prediction_stabilization": dict(prediction_stabilization),
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
                "direction_calibration": self.direction_calibration,
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
        self.direction_calibration = dict(meta.get("direction_calibration", {}) or {})
        if not self.direction_calibration:
            self.direction_calibration = dict(getattr(self.direction_model, "calibration", {}) or {})
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
