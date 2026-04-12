from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_fscore_support

from catboost_floader.core.config import (
    DIRECTION_CALIBRATION_CONFIDENCE_GRID,
    DIRECTION_CALIBRATION_DEADBAND_GRID,
    DIRECTION_CALIBRATION_ENABLED,
    DIRECTION_CALIBRATION_MAIN_ONLY,
    DIRECTION_CALIBRATION_MAIN_RECENT_FRACTION,
    DIRECTION_CALIBRATION_MAIN_RECENT_METRIC_TOLERANCE,
    DIRECTION_CALIBRATION_MAX_NEUTRAL_OVERPREDICTION,
    DIRECTION_CALIBRATION_MIN_NEUTRAL_RECALL,
    DIRECTION_CALIBRATION_MIN_UNIQUE_PREDICTED_CLASSES,
    MAIN_DIRECT_PERSISTENCE_PROMOTION_ENABLED,
    MAIN_DIRECT_PERSISTENCE_PROMOTION_MAE_TOLERANCE_RATIO,
    MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_DELTA_VS_PERSISTENCE,
    MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_RECENT_DELTA_VS_PERSISTENCE,
    MAIN_DIRECT_PERSISTENCE_PROMOTION_RECENT_FRACTION,
    MAIN_SELECTION_ALLOW_NEGATIVE_DELTA,
    MAIN_SELECTION_NEGATIVE_DELTA_TOLERANCE,
    OVERFIT_STABILIZATION_ALPHA_CAP_MODERATE,
    OVERFIT_STABILIZATION_CONFIDENCE_BUMP_MODERATE,
    OVERFIT_STABILIZATION_CONFIDENCE_BUMP_SEVERE,
    OVERFIT_STABILIZATION_ENABLED,
    OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_MODERATE,
    OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_SEVERE,
    OVERFIT_STABILIZATION_HIGH_ALPHA_AGGRESSIVENESS_BONUS,
    OVERFIT_STABILIZATION_HIGH_ALPHA_THRESHOLD,
    OVERFIT_STABILIZATION_HOLDOUT_RATIO_WEIGHT,
    OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_MODERATE,
    OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_SEVERE,
    OVERFIT_STABILIZATION_MAE_GAP_WEIGHT,
    OVERFIT_STABILIZATION_MODEL_ONLY_AGGRESSIVENESS_BONUS,
    OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_MODERATE,
    OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_SEVERE,
    OVERFIT_STABILIZATION_OVERFIT_PENALTY_MAX,
    OVERFIT_STABILIZATION_OVERFIT_PENALTY_SCALE,
    OVERFIT_STABILIZATION_PRIMARY_ALPHA_CAP_SEVERE,
    OVERFIT_STABILIZATION_PRIMARY_MODELS,
    OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_MODERATE,
    OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_SEVERE,
    OVERFIT_STABILIZATION_SECONDARY_ALPHA_CAP_SEVERE,
    OVERFIT_STABILIZATION_SIGN_GAP_MIN,
    OVERFIT_STABILIZATION_SIGN_GAP_WEIGHT,
    OVERFIT_STABILIZATION_TARGET_MODELS,
    DIRECTION_DEADBAND,
    DIRECTION_PRED_THRESHOLD,
    DIRECT_STRATEGY_ROBUSTNESS_ENABLED,
    DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT,
    DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO,
)
from catboost_floader.core.utils import _drop_non_model_columns
from catboost_floader.diagnostics.artifact_readers import load_model_backtest_summary
from catboost_floader.evaluation.backtest import build_direct_baselines
from catboost_floader.models.direct import resolve_direct_composition_config

from catboost_floader.selection.composition_profiles import (
    _direct_strategy_alpha_grid,
    _direct_profile_key,
    _direct_profile_sequence,
    _direct_strategy_candidates,
    _direct_strategy_model_weight,
)
from catboost_floader.selection.direct_robustness import (
    _compute_direct_candidate_multi_window,
    _extract_robustness_metrics,
    _robustness_comparison_key,
    _direct_strategy_passes_robustness,
)
from catboost_floader.selection.direct_strategy_guard import _apply_persistence_guard


def _direction_labels_from_target_return(target_return: np.ndarray, deadband: float) -> np.ndarray:
    labels = np.sign(np.asarray(target_return, dtype=float)).astype(int)
    labels[np.abs(target_return) < float(deadband)] = 0
    return labels


def _direction_labels_from_components(
    probs: np.ndarray,
    class_signs: np.ndarray,
    expectation: np.ndarray,
    *,
    confidence_threshold: float,
    deadband: float,
) -> np.ndarray:
    probs_arr = np.asarray(probs, dtype=float)
    class_signs_arr = np.asarray(class_signs, dtype=float)
    expectation_arr = np.asarray(expectation, dtype=float)

    if probs_arr.ndim == 2 and probs_arr.shape[1] > 0:
        max_p = np.nanmax(probs_arr, axis=1)
        top_idx = np.argmax(probs_arr, axis=1)
        pred_labels = class_signs_arr[top_idx].astype(int)
        pred_labels[max_p < float(confidence_threshold)] = 0
    else:
        pred_labels = np.sign(expectation_arr).astype(int)

    pred_labels[np.abs(expectation_arr) < float(deadband)] = 0
    return pred_labels


def _direction_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    y_true_arr = np.asarray(y_true, dtype=int)
    y_pred_arr = np.asarray(y_pred, dtype=int)

    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true_arr,
        y_pred_arr,
        labels=[-1, 0, 1],
        zero_division=0,
    )
    macro_f1 = float(np.mean(f1))
    sign_accuracy = float(np.mean(np.sign(y_true_arr) == np.sign(y_pred_arr))) if len(y_true_arr) else 0.0
    neutral_true_rate = float(np.mean(y_true_arr == 0)) if len(y_true_arr) else 0.0
    neutral_pred_rate = float(np.mean(y_pred_arr == 0)) if len(y_pred_arr) else 0.0
    neutral_overprediction = float(max(0.0, neutral_pred_rate - neutral_true_rate))
    unique_predicted_classes = int(np.unique(y_pred_arr).size) if len(y_pred_arr) else 0
    return {
        "macro_f1": macro_f1,
        "sign_accuracy": sign_accuracy,
        "neutral_precision": float(precision[1]),
        "neutral_recall": float(recall[1]),
        "neutral_f1": float(f1[1]),
        "neutral_true_rate": neutral_true_rate,
        "neutral_pred_rate": neutral_pred_rate,
        "neutral_overprediction": neutral_overprediction,
        "unique_predicted_classes": unique_predicted_classes,
    }


def _recent_slice_metrics(y_true: np.ndarray, y_pred: np.ndarray, recent_fraction: float) -> Dict[str, float]:
    n = int(len(y_true))
    if n <= 0:
        return _direction_metrics(y_true, y_pred)
    frac = min(1.0, max(0.05, float(recent_fraction)))
    tail = max(1, int(round(n * frac)))
    return _direction_metrics(y_true[-tail:], y_pred[-tail:])


def _direction_metrics_key(metrics: Dict[str, float]) -> tuple[float, float, float, float]:
    return (
        float(metrics.get("macro_f1", 0.0)),
        float(metrics.get("sign_accuracy", 0.0)),
        float(metrics.get("neutral_f1", 0.0)),
        -float(metrics.get("neutral_overprediction", 0.0)),
    )


def _direction_metrics_non_regression(
    metrics: Dict[str, float],
    baseline_metrics: Dict[str, float],
    *,
    tolerance: float,
) -> bool:
    for metric_name in ("macro_f1", "neutral_f1", "sign_accuracy"):
        if float(metrics.get(metric_name, 0.0)) < float(baseline_metrics.get(metric_name, 0.0)) - float(tolerance):
            return False
    return True


def _direction_metrics_strict_improvement(
    metrics: Dict[str, float],
    baseline_metrics: Dict[str, float],
) -> bool:
    return any(
        float(metrics.get(metric_name, 0.0)) > float(baseline_metrics.get(metric_name, 0.0)) + 1e-12
        for metric_name in ("macro_f1", "neutral_f1", "sign_accuracy")
    )


def _calibrate_direction_thresholds(
    *,
    direct_model,
    X_model_aligned: pd.DataFrame,
    target_return: np.ndarray,
) -> Dict[str, Any]:
    active_profile_key = _direct_profile_key(getattr(direct_model, "composition_profile", None))
    default_threshold = float(
        dict(getattr(direct_model, "composition_config", {}) or {}).get(
            "label_confidence_threshold",
            DIRECTION_PRED_THRESHOLD,
        )
    )
    default_deadband = float(DIRECTION_DEADBAND)

    payload: Dict[str, Any] = {
        "enabled": bool(DIRECTION_CALIBRATION_ENABLED),
        "main_only": bool(DIRECTION_CALIBRATION_MAIN_ONLY),
        "applied": False,
        "active_profile": active_profile_key,
        "selected_confidence_threshold": default_threshold,
        "selected_deadband": default_deadband,
        "reason": "disabled",
        "search_space": {
            "deadband": [float(x) for x in DIRECTION_CALIBRATION_DEADBAND_GRID],
            "confidence_threshold": [float(x) for x in DIRECTION_CALIBRATION_CONFIDENCE_GRID],
        },
        "max_neutral_overprediction": float(DIRECTION_CALIBRATION_MAX_NEUTRAL_OVERPREDICTION),
        "min_unique_predicted_classes": int(DIRECTION_CALIBRATION_MIN_UNIQUE_PREDICTED_CLASSES),
        "min_neutral_recall": float(DIRECTION_CALIBRATION_MIN_NEUTRAL_RECALL),
        "evaluations": [],
        "baseline_metrics": {},
        "selected_metrics": {},
        "baseline_recent_metrics": {},
        "selected_recent_metrics": {},
        "applies_to_profiles": ["main_direct_pipeline"] if bool(DIRECTION_CALIBRATION_MAIN_ONLY) else ["main_direct_pipeline", "default"],
    }

    if not bool(DIRECTION_CALIBRATION_ENABLED):
        return payload
    if bool(DIRECTION_CALIBRATION_MAIN_ONLY) and active_profile_key != "main_direct_pipeline":
        payload["reason"] = "profile_excluded"
        return payload

    try:
        components = direct_model.direction_model.predict_components(X_model_aligned)
        probs = np.asarray(components.get("probs"), dtype=float)
        class_signs = np.asarray(components.get("class_signs"), dtype=float)
        expectation = np.asarray(components.get("expectation"), dtype=float)
    except Exception:
        payload["reason"] = "components_unavailable"
        return payload

    if len(expectation) == 0 or len(target_return) == 0:
        payload["reason"] = "empty_validation_data"
        return payload

    true_default = _direction_labels_from_target_return(target_return, default_deadband)
    pred_default = _direction_labels_from_components(
        probs,
        class_signs,
        expectation,
        confidence_threshold=default_threshold,
        deadband=default_deadband,
    )
    payload["baseline_metrics"] = _direction_metrics(true_default, pred_default)
    payload["baseline_recent_metrics"] = _recent_slice_metrics(
        true_default,
        pred_default,
        recent_fraction=float(DIRECTION_CALIBRATION_MAIN_RECENT_FRACTION),
    )
    baseline_metrics = dict(payload.get("baseline_metrics", {}) or {})
    baseline_recent_metrics = dict(payload.get("baseline_recent_metrics", {}) or {})
    metric_tolerance = float(DIRECTION_CALIBRATION_MAIN_RECENT_METRIC_TOLERANCE)
    min_neutral_recall = float(DIRECTION_CALIBRATION_MIN_NEUTRAL_RECALL)

    grid_deadband = [float(v) for v in DIRECTION_CALIBRATION_DEADBAND_GRID]
    grid_threshold = [float(v) for v in DIRECTION_CALIBRATION_CONFIDENCE_GRID]
    if not grid_deadband:
        grid_deadband = [default_deadband]
    if not grid_threshold:
        grid_threshold = [default_threshold]

    best_entry: Dict[str, Any] | None = None
    best_relaxed_entry: Dict[str, Any] | None = None
    any_validation_gate_pass = False
    any_recent_gate_pass = False

    for deadband in grid_deadband:
        y_true = _direction_labels_from_target_return(target_return, deadband)
        for threshold in grid_threshold:
            y_pred = _direction_labels_from_components(
                probs,
                class_signs,
                expectation,
                confidence_threshold=threshold,
                deadband=deadband,
            )
            metrics = _direction_metrics(y_true, y_pred)
            recent_metrics = _recent_slice_metrics(
                y_true,
                y_pred,
                recent_fraction=float(DIRECTION_CALIBRATION_MAIN_RECENT_FRACTION),
            )
            valid_constraints = (
                metrics["neutral_overprediction"] <= float(DIRECTION_CALIBRATION_MAX_NEUTRAL_OVERPREDICTION)
                and metrics["unique_predicted_classes"] >= int(DIRECTION_CALIBRATION_MIN_UNIQUE_PREDICTED_CLASSES)
            )
            validation_gate_reasons: list[str] = []
            recent_gate_reasons: list[str] = []
            validation_gate_pass = bool(valid_constraints)
            recent_gate_pass = bool(valid_constraints)
            if not valid_constraints:
                validation_gate_reasons.append("constraints_not_met")
                recent_gate_reasons.append("constraints_not_met")
            else:
                if not _direction_metrics_non_regression(metrics, baseline_metrics, tolerance=metric_tolerance):
                    validation_gate_reasons.append("validation_non_regression_failed")
                # Neutral recall is only meaningful when the slice actually contains neutral examples.
                if float(metrics.get("neutral_true_rate", 0.0)) > 0.0 and float(metrics.get("neutral_recall", 0.0)) < min_neutral_recall:
                    validation_gate_reasons.append("validation_neutral_recall_below_floor")
                if not _direction_metrics_strict_improvement(metrics, baseline_metrics):
                    validation_gate_reasons.append("validation_no_strict_improvement")
                validation_gate_pass = not validation_gate_reasons

                if not _direction_metrics_non_regression(recent_metrics, baseline_recent_metrics, tolerance=metric_tolerance):
                    recent_gate_reasons.append("recent_non_regression_failed")
                if float(recent_metrics.get("neutral_true_rate", 0.0)) > 0.0 and float(recent_metrics.get("neutral_recall", 0.0)) < min_neutral_recall:
                    recent_gate_reasons.append("recent_neutral_recall_below_floor")
                if not _direction_metrics_strict_improvement(recent_metrics, baseline_recent_metrics):
                    recent_gate_reasons.append("recent_no_strict_improvement")
                recent_gate_pass = not recent_gate_reasons
            entry = {
                "deadband": float(deadband),
                "confidence_threshold": float(threshold),
                "metrics": metrics,
                "recent_metrics": recent_metrics,
                "constraints_passed": bool(valid_constraints),
                "validation_gate_pass": bool(validation_gate_pass),
                "validation_gate_reasons": list(validation_gate_reasons),
                "recent_gate_pass": bool(recent_gate_pass),
                "recent_gate_reasons": list(recent_gate_reasons),
                "main_recent_gate_passed": bool(validation_gate_pass and recent_gate_pass),
            }
            payload["evaluations"].append(entry)

            if best_relaxed_entry is None:
                best_relaxed_entry = entry
            else:
                cand_key = _direction_metrics_key(metrics)
                best_relaxed_key = _direction_metrics_key(best_relaxed_entry["metrics"])
                if cand_key > best_relaxed_key:
                    best_relaxed_entry = entry

            if not validation_gate_pass:
                continue
            any_validation_gate_pass = True
            if not recent_gate_pass:
                continue
            any_recent_gate_pass = True

            if best_entry is None:
                best_entry = entry
                continue
            cand_key = _direction_metrics_key(metrics)
            best_key = _direction_metrics_key(best_entry["metrics"])
            if cand_key > best_key:
                best_entry = entry

    if best_entry is None:
        if best_relaxed_entry is None:
            payload["reason"] = "search_failed"
            return payload
        if not any_validation_gate_pass:
            payload["reason"] = "main_validation_gate_not_met"
        elif not any_recent_gate_pass:
            payload["reason"] = "main_recent_gate_not_met"
        else:
            payload["reason"] = "main_quality_gate_not_met"
        payload["selected_metrics"] = dict(payload.get("baseline_metrics", {}) or {})
        payload["selected_recent_metrics"] = dict(payload.get("baseline_recent_metrics", {}) or {})
        payload["applied"] = False
        return payload

    selected = best_entry
    if selected is None:
        payload["reason"] = "search_failed"
        return payload

    payload["selected_deadband"] = float(selected["deadband"])
    payload["selected_confidence_threshold"] = float(selected["confidence_threshold"])
    payload["selected_metrics"] = dict(selected["metrics"])
    payload["selected_recent_metrics"] = dict(selected.get("recent_metrics", {}) or {})
    payload["applied"] = True
    payload["reason"] = "calibrated"
    return payload


def _main_persistence_promotion_policy(direct_model) -> Dict[str, Any]:
    active_profile_key = _direct_profile_key(getattr(direct_model, "composition_profile", None))
    enabled = bool(MAIN_DIRECT_PERSISTENCE_PROMOTION_ENABLED) and active_profile_key == "main_direct_pipeline"
    return {
        "enabled": enabled,
        "active_profile": active_profile_key,
        "recent_fraction": float(MAIN_DIRECT_PERSISTENCE_PROMOTION_RECENT_FRACTION),
        "min_delta_vs_persistence": float(MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_DELTA_VS_PERSISTENCE),
        "min_recent_delta_vs_persistence": float(MAIN_DIRECT_PERSISTENCE_PROMOTION_MIN_RECENT_DELTA_VS_PERSISTENCE),
        "mae_tolerance_ratio": float(MAIN_DIRECT_PERSISTENCE_PROMOTION_MAE_TOLERANCE_RATIO),
    }


def _try_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _main_relaxed_selection_policy(profile_key: str) -> Dict[str, Any]:
    tolerance = max(0.0, float(MAIN_SELECTION_NEGATIVE_DELTA_TOLERANCE))
    enabled = bool(MAIN_SELECTION_ALLOW_NEGATIVE_DELTA) and str(profile_key) == "main_direct_pipeline"
    return {
        "enabled": enabled,
        "profile": str(profile_key),
        "negative_delta_tolerance": tolerance,
    }


def _is_non_baseline_strategy(strategy: Dict[str, Any] | None) -> bool:
    return str(dict(strategy or {}).get("type", "model_only")) != "baseline_only"


def _candidate_within_main_negative_tolerance(candidate_eval: Dict[str, Any], policy: Dict[str, Any]) -> bool:
    if not bool(policy.get("enabled", False)):
        return False
    rel_delta = _try_float(candidate_eval.get("relative_delta_vs_persistence"))
    if rel_delta is None:
        return False
    return rel_delta >= -float(policy.get("negative_delta_tolerance", 0.0))


def _load_previous_overfitting_diagnostics(model_key: str | None) -> Dict[str, Any]:
    summary_payload = dict(load_model_backtest_summary(model_key) or {})
    diagnostics = dict(summary_payload.get("overfitting_diagnostics", {}) or {})
    if diagnostics:
        return diagnostics

    fields = [
        "train_MAE",
        "val_MAE",
        "holdout_MAE",
        "train_sign_acc",
        "val_sign_acc",
        "holdout_sign_acc",
        "mae_gap_train_val",
        "mae_gap_train_holdout",
        "sign_gap_train_val",
        "sign_gap_train_holdout",
        "mae_overfit_ratio",
        "holdout_overfit_ratio",
        "overfit_status",
        "overfit_reason",
        "train_delta_vs_baseline",
        "val_delta_vs_baseline",
        "holdout_delta_vs_baseline",
    ]
    flattened = {field: summary_payload.get(field) for field in fields}
    if any(value is not None for value in flattened.values()):
        return flattened
    return {}


def _resolve_target_overfit_stabilization_context(model_key: str | None) -> Dict[str, Any]:
    model_key_norm = "" if model_key is None else str(model_key)
    target_models = {str(item) for item in list(OVERFIT_STABILIZATION_TARGET_MODELS)}
    primary_models = {str(item) for item in list(OVERFIT_STABILIZATION_PRIMARY_MODELS)}

    context: Dict[str, Any] = {
        "enabled": False,
        "reason": "disabled",
        "model_key": model_key_norm,
        "targeted_model": model_key_norm in target_models,
        "primary_model": model_key_norm in primary_models,
        "severity": "none",
        "overfit_status": "none",
        "overfit_reason": "unavailable",
        "diagnostics": {},
        "reference_overfit": {},
        "policy": {},
    }

    if not bool(OVERFIT_STABILIZATION_ENABLED):
        return context
    if model_key_norm not in target_models:
        context["reason"] = "not_target_model"
        return context

    diagnostics = _load_previous_overfitting_diagnostics(model_key_norm)
    context["diagnostics"] = dict(diagnostics)
    overfit_status = str(diagnostics.get("overfit_status", "none") or "none").lower()
    overfit_reason = str(diagnostics.get("overfit_reason", "unavailable") or "unavailable")
    sign_gap_train_holdout = max(0.0, float(_try_float(diagnostics.get("sign_gap_train_holdout")) or 0.0))

    severity = "none"
    if overfit_status == "severe":
        severity = "severe"
    elif overfit_status == "moderate" or sign_gap_train_holdout >= float(OVERFIT_STABILIZATION_SIGN_GAP_MIN):
        severity = "moderate"

    context["severity"] = severity
    context["overfit_status"] = overfit_status
    context["overfit_reason"] = overfit_reason
    context["reference_overfit"] = {
        "overfit_status": overfit_status,
        "overfit_reason": overfit_reason,
        "holdout_overfit_ratio": _try_float(diagnostics.get("holdout_overfit_ratio")),
        "mae_gap_train_holdout": _try_float(diagnostics.get("mae_gap_train_holdout")),
        "sign_gap_train_holdout": _try_float(diagnostics.get("sign_gap_train_holdout")),
    }

    if severity not in {"severe", "moderate"}:
        context["reason"] = "overfit_signal_below_threshold"
        return context

    is_primary = bool(context["primary_model"])
    if severity == "severe":
        alpha_cap = (
            float(OVERFIT_STABILIZATION_PRIMARY_ALPHA_CAP_SEVERE)
            if is_primary
            else float(OVERFIT_STABILIZATION_SECONDARY_ALPHA_CAP_SEVERE)
        )
        confidence_bump = float(OVERFIT_STABILIZATION_CONFIDENCE_BUMP_SEVERE)
        movement_scale_cap = float(OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_SEVERE)
        expectation_deadband_floor = float(OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_SEVERE)
        low_conf_weight_cap = float(OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_SEVERE)
        promotion_max_penalty = float(OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_SEVERE)
    else:
        alpha_cap = float(OVERFIT_STABILIZATION_ALPHA_CAP_MODERATE)
        confidence_bump = float(OVERFIT_STABILIZATION_CONFIDENCE_BUMP_MODERATE)
        movement_scale_cap = float(OVERFIT_STABILIZATION_MOVEMENT_SCALE_CAP_MODERATE)
        expectation_deadband_floor = float(OVERFIT_STABILIZATION_EXPECTATION_DEADBAND_FLOOR_MODERATE)
        low_conf_weight_cap = float(OVERFIT_STABILIZATION_LOW_CONFIDENCE_EXPECTATION_WEIGHT_CAP_MODERATE)
        promotion_max_penalty = float(OVERFIT_STABILIZATION_PROMOTION_MAX_PENALTY_MODERATE)

    context["enabled"] = True
    context["reason"] = "active"
    context["policy"] = {
        "severity": severity,
        "alpha_cap": alpha_cap,
        "confidence_bump": confidence_bump,
        "movement_scale_cap": movement_scale_cap,
        "expectation_deadband_floor": expectation_deadband_floor,
        "low_confidence_expectation_weight_cap": low_conf_weight_cap,
        "promotion_max_penalty_ratio": promotion_max_penalty,
        "penalty_scale": float(OVERFIT_STABILIZATION_OVERFIT_PENALTY_SCALE),
        "penalty_max": float(OVERFIT_STABILIZATION_OVERFIT_PENALTY_MAX),
    }
    return context


def _apply_targeted_stabilization_to_strategy_config(
    strategy_cfg: Dict[str, Any],
    stabilization_context: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    cfg = dict(strategy_cfg)
    policy = dict(stabilization_context.get("policy", {}) or {})
    payload: Dict[str, Any] = {
        "applied": False,
        "severity": str(stabilization_context.get("severity", "none")),
        "changes": {},
    }

    if not bool(stabilization_context.get("enabled", False)):
        payload["reason"] = "stabilization_inactive"
        return cfg, payload

    confidence_before = float(cfg.get("label_confidence_threshold", DIRECTION_PRED_THRESHOLD))
    confidence_after = min(0.9, confidence_before + float(policy.get("confidence_bump", 0.0)))
    if confidence_after > confidence_before + 1e-12:
        cfg["label_confidence_threshold"] = confidence_after
        payload["changes"]["label_confidence_threshold"] = {
            "before": confidence_before,
            "after": confidence_after,
        }

    movement_before = float(cfg.get("movement_scale", 1.0))
    movement_after = min(movement_before, float(policy.get("movement_scale_cap", movement_before)))
    if movement_after < movement_before - 1e-12:
        cfg["movement_scale"] = movement_after
        payload["changes"]["movement_scale"] = {"before": movement_before, "after": movement_after}

    deadband_before = float(cfg.get("expectation_deadband", 0.0))
    deadband_after = max(deadband_before, float(policy.get("expectation_deadband_floor", deadband_before)))
    if deadband_after > deadband_before + 1e-12:
        cfg["expectation_deadband"] = deadband_after
        payload["changes"]["expectation_deadband"] = {"before": deadband_before, "after": deadband_after}

    low_conf_weight_before = float(cfg.get("low_confidence_expectation_weight", 1.0))
    low_conf_weight_after = min(
        low_conf_weight_before,
        float(policy.get("low_confidence_expectation_weight_cap", low_conf_weight_before)),
    )
    if low_conf_weight_after < low_conf_weight_before - 1e-12:
        cfg["low_confidence_expectation_weight"] = low_conf_weight_after
        payload["changes"]["low_confidence_expectation_weight"] = {
            "before": low_conf_weight_before,
            "after": low_conf_weight_after,
        }

    if str(cfg.get("low_confidence_sign_mode", "neutral")) == "expectation" and str(
        stabilization_context.get("severity", "none")
    ) == "severe":
        cfg["low_confidence_sign_mode"] = "blend"
        payload["changes"]["low_confidence_sign_mode"] = {
            "before": "expectation",
            "after": "blend",
        }

    prefer_model_tolerance_before = float(cfg.get("strategy_prefer_model_tolerance", 0.0))
    if prefer_model_tolerance_before > 0.0:
        cfg["strategy_prefer_model_tolerance"] = 0.0
        payload["changes"]["strategy_prefer_model_tolerance"] = {
            "before": prefer_model_tolerance_before,
            "after": 0.0,
        }

    alpha_cap = max(0.05, min(0.99, float(policy.get("alpha_cap", 0.99))))
    alpha_grid_before = [float(alpha) for alpha in _direct_strategy_alpha_grid(cfg)]
    alpha_grid_after = [alpha for alpha in alpha_grid_before if alpha <= alpha_cap + 1e-12]
    if not alpha_grid_after:
        alpha_grid_after = [round(alpha_cap, 3)]
    if alpha_grid_after != alpha_grid_before:
        cfg["strategy_alpha_grid"] = alpha_grid_after
        payload["changes"]["strategy_alpha_grid"] = {
            "before": alpha_grid_before,
            "after": alpha_grid_after,
        }

    payload["applied"] = bool(payload["changes"])
    payload["reason"] = "targeted_overfit_stabilization" if payload["applied"] else "no_config_delta"
    return cfg, payload


def _candidate_overfit_penalty_ratio(
    candidate_eval: Dict[str, Any],
    stabilization_context: Dict[str, Any],
) -> float:
    if not bool(stabilization_context.get("enabled", False)):
        return 0.0

    diagnostics = dict(stabilization_context.get("diagnostics", {}) or {})
    overfit_status = str(stabilization_context.get("overfit_status", "none"))
    status_bias_map = {"none": 0.0, "mild": 0.25, "moderate": 0.6, "severe": 1.0}
    status_bias = float(status_bias_map.get(overfit_status, 0.0))

    holdout_ratio = max(0.0, float(_try_float(diagnostics.get("holdout_overfit_ratio")) or 1.0) - 1.0)
    sign_gap = max(0.0, float(_try_float(diagnostics.get("sign_gap_train_holdout")) or 0.0))
    mae_gap = float(_try_float(diagnostics.get("mae_gap_train_holdout")) or 0.0)
    holdout_mae = abs(float(_try_float(diagnostics.get("holdout_MAE")) or 0.0))
    normalized_mae_gap = max(0.0, mae_gap / max(1.0, holdout_mae))

    risk_score = (
        status_bias
        + holdout_ratio * float(OVERFIT_STABILIZATION_HOLDOUT_RATIO_WEIGHT)
        + sign_gap * float(OVERFIT_STABILIZATION_SIGN_GAP_WEIGHT)
        + normalized_mae_gap * float(OVERFIT_STABILIZATION_MAE_GAP_WEIGHT)
    )

    strategy = dict(candidate_eval.get("strategy", {}) or {})
    strategy_type = str(strategy.get("type", "model_only"))
    model_weight = _direct_strategy_model_weight(strategy)
    aggressiveness = max(0.0, model_weight)
    if strategy_type == "model_only":
        aggressiveness += float(OVERFIT_STABILIZATION_MODEL_ONLY_AGGRESSIVENESS_BONUS)
    if strategy_type == "blend" and float(strategy.get("alpha", 0.0)) >= float(OVERFIT_STABILIZATION_HIGH_ALPHA_THRESHOLD):
        aggressiveness += float(OVERFIT_STABILIZATION_HIGH_ALPHA_AGGRESSIVENESS_BONUS)

    relative_delta = max(0.0, float(_try_float(candidate_eval.get("relative_delta_vs_persistence")) or 0.0))
    edge_relief = max(0.6, 1.0 - 2.0 * min(relative_delta, 0.2))

    penalty_ratio = float(OVERFIT_STABILIZATION_OVERFIT_PENALTY_SCALE) * risk_score * aggressiveness * edge_relief
    penalty_ratio = max(0.0, min(float(OVERFIT_STABILIZATION_OVERFIT_PENALTY_MAX), penalty_ratio))
    return penalty_ratio


def _candidate_ranking_mae(candidate_eval: Dict[str, Any]) -> float:
    effective = _try_float(candidate_eval.get("stabilization_effective_mae"))
    if effective is not None:
        return effective
    fallback = _try_float(candidate_eval.get("validation_mae"))
    return float(fallback if fallback is not None else np.inf)


def _profile_result_ranking_mae(profile_result: Dict[str, Any]) -> float:
    selected = dict(profile_result.get("selected", {}) or {})
    effective = _try_float(selected.get("stabilization_effective_mae"))
    if effective is not None:
        return effective
    fallback = _try_float(profile_result.get("validation_mae"))
    return float(fallback if fallback is not None else np.inf)


def _stabilization_strategy_fields(stabilization_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "stabilization_model_key": stabilization_context.get("model_key"),
        "stabilization_targeted_model": bool(stabilization_context.get("targeted_model", False)),
        "stabilization_applied": bool(stabilization_context.get("enabled", False)),
        "stabilization_overfit_status": stabilization_context.get("overfit_status"),
        "stabilization_overfit_reason": stabilization_context.get("overfit_reason"),
        "stabilization_reference_overfit": dict(stabilization_context.get("reference_overfit", {}) or {}),
        "stabilization_policy": dict(stabilization_context.get("policy", {}) or {}),
    }


def _select_direct_strategy(
    direct_model,
    X_val_full: pd.DataFrame,
    y_val: pd.DataFrame,
    *,
    model_key: str | None = None,
) -> Dict[str, object]:
    if X_val_full.empty or y_val.empty:
        return {
            "type": "model_only",
            "alpha": 1.0,
            "baseline": "persistence",
            **_stabilization_strategy_fields(_resolve_target_overfit_stabilization_context(model_key)),
        }

    stabilization_context = _resolve_target_overfit_stabilization_context(model_key)

    X_model = _drop_non_model_columns(X_val_full)
    X_model_aligned = X_model.reindex(columns=direct_model.feature_names, fill_value=0.0)
    close = pd.to_numeric(X_val_full["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_price = pd.to_numeric(y_val["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_return = pd.to_numeric(y_val["target_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    baselines = build_direct_baselines(X_val_full)
    baseline_map = {
        "persistence": pd.to_numeric(baselines["baseline_persistence_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "rolling_mean": pd.to_numeric(baselines["baseline_rolling_mean_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        "trend": pd.to_numeric(baselines["baseline_trend_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
    }

    direction_calibration = _calibrate_direction_thresholds(
        direct_model=direct_model,
        X_model_aligned=X_model_aligned,
        target_return=target_return,
    )
    direct_model.direction_calibration = dict(direction_calibration)
    if hasattr(direct_model, "direction_model"):
        direct_model.direction_model.calibration = dict(direction_calibration)

    persistence_price = close * (1.0 + baseline_map["persistence"])
    persistence_abs_err = np.abs(target_price - persistence_price)
    persistence_mae = float(np.mean(persistence_abs_err))
    promotion_policy = _main_persistence_promotion_policy(direct_model)
    active_profile_key = _direct_profile_key(getattr(direct_model, "composition_profile", None))
    recent_tail = max(1, int(round(len(target_price) * float(promotion_policy["recent_fraction"]))))
    persistence_recent_mae = float(np.mean(persistence_abs_err[-recent_tail:]))

    profile_sequence_keys: list[str] = []
    profile_results: dict[str, Dict[str, Any]] = {}
    for profile_name in _direct_profile_sequence(direct_model):
        base_strategy_cfg = resolve_direct_composition_config(profile_name)
        strategy_cfg, stabilization_cfg_overrides = _apply_targeted_stabilization_to_strategy_config(
            base_strategy_cfg,
            stabilization_context,
        )
        profile_key = _direct_profile_key(profile_name)
        profile_sequence_keys.append(profile_key)
        if not bool(strategy_cfg.get("profile_enabled", True)):
            profile_results[profile_key] = {
                "profile": profile_key,
                "config": dict(strategy_cfg),
                "stabilization_config_overrides": dict(stabilization_cfg_overrides),
                "validation_mae": None,
                "status": "config_disabled",
                "selected": None,
                "candidate_count": 0,
                "robust_candidate_count": 0,
                "candidate_evaluations": [],
            }
            continue
        direct_details = direct_model.predict_details(
            X_model_aligned,
            composition_profile=profile_name,
            composition_config=strategy_cfg,
        )
        raw_pred = np.asarray(direct_details["raw_pred_return"], dtype=float)
        prefer_model_tol = float(strategy_cfg.get("strategy_prefer_model_tolerance", 0.0))
        candidates = _direct_strategy_candidates(strategy_cfg)
        candidate_evaluations: list[Dict[str, Any]] = []

        for strategy in candidates:
            if strategy["type"] == "model_only":
                ret = raw_pred
            elif strategy["type"] == "baseline_only":
                ret = baseline_map.get(str(strategy["baseline"]), baseline_map["persistence"])
            else:
                base = baseline_map.get(str(strategy["baseline"]), baseline_map["persistence"])
                ret = strategy["alpha"] * raw_pred + (1.0 - strategy["alpha"]) * base
            pred_price = close * (1.0 + ret)
            abs_err = np.abs(target_price - pred_price)
            mae = float(np.mean(np.abs(target_price - pred_price)))
            recent_mae = float(np.mean(abs_err[-recent_tail:]))
            delta_vs_persistence = float(persistence_mae - mae)
            recent_delta_vs_persistence = float(persistence_recent_mae - recent_mae)
            relative_delta_vs_persistence = float(delta_vs_persistence / max(abs(persistence_mae), 1e-8))
            relative_recent_delta_vs_persistence = float(
                recent_delta_vs_persistence / max(abs(persistence_recent_mae), 1e-8)
            )
            multi_window_summary = _compute_direct_candidate_multi_window(
                model_key=f"direct_selection:{profile_key}",
                close=close,
                target_price=target_price,
                target_return=target_return,
                pred_return=np.asarray(ret, dtype=float),
                baseline_persistence_return=baseline_map["persistence"],
            )
            robustness_metrics = _extract_robustness_metrics(multi_window_summary)
            robustness_gate_pass, robustness_gate_reasons = _direct_strategy_passes_robustness(robustness_metrics)
            raw_candidate_eval = {
                "strategy": dict(strategy),
                "validation_mae": float(mae),
                "delta_vs_persistence": delta_vs_persistence,
                "recent_delta_vs_persistence": recent_delta_vs_persistence,
                "relative_delta_vs_persistence": relative_delta_vs_persistence,
                "relative_recent_delta_vs_persistence": relative_recent_delta_vs_persistence,
            }
            overfit_penalty_ratio = _candidate_overfit_penalty_ratio(raw_candidate_eval, stabilization_context)
            effective_mae = float(mae * (1.0 + overfit_penalty_ratio))
            candidate_evaluations.append(
                {
                    "strategy": dict(strategy),
                    "validation_mae": float(mae),
                    "stabilization_overfit_penalty_ratio": overfit_penalty_ratio,
                    "stabilization_effective_mae": effective_mae,
                    "stabilization_aggressiveness": _direct_strategy_model_weight(dict(strategy)),
                    "robustness_metrics": robustness_metrics,
                    "robustness_summary": multi_window_summary,
                    "robustness_gate_pass": bool(robustness_gate_pass),
                    "robustness_gate_reasons": list(robustness_gate_reasons),
                    "delta_vs_persistence": delta_vs_persistence,
                    "recent_delta_vs_persistence": recent_delta_vs_persistence,
                    "relative_delta_vs_persistence": relative_delta_vs_persistence,
                    "relative_recent_delta_vs_persistence": relative_recent_delta_vs_persistence,
                    "recent_mae": recent_mae,
                }
            )

        if not candidate_evaluations:
            profile_results[profile_key] = {
                "profile": profile_key,
                "config": dict(strategy_cfg),
                "stabilization_config_overrides": dict(stabilization_cfg_overrides),
                "validation_mae": None,
                "status": "no_candidates",
                "selected": None,
                "candidate_count": 0,
                "robust_candidate_count": 0,
                "candidate_evaluations": [],
            }
            continue

        robust_candidates = [c for c in candidate_evaluations if c["robustness_gate_pass"]]
        selection_pool = robust_candidates if robust_candidates else candidate_evaluations
        selection_pool_name = "robustness_gate_pass" if robust_candidates else "all_candidates_fallback"
        relaxed_selection_policy = _main_relaxed_selection_policy(profile_key)

        profile_best = selection_pool[0]
        for candidate in selection_pool[1:]:
            candidate_mae = _candidate_ranking_mae(candidate)
            best_mae = _candidate_ranking_mae(profile_best)
            mae_close_tol = max(max(best_mae, candidate_mae), 1e-8) * float(DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO)

            if candidate_mae < best_mae - 1e-12:
                profile_best = candidate
                continue

            if abs(candidate_mae - best_mae) <= max(mae_close_tol, 1e-12):
                cand_rob = _robustness_comparison_key(candidate["robustness_metrics"])
                best_rob = _robustness_comparison_key(profile_best["robustness_metrics"])
                if cand_rob > best_rob:
                    profile_best = candidate
                    continue
                if cand_rob == best_rob:
                    if bool(relaxed_selection_policy.get("enabled", False)):
                        candidate_competitive_non_baseline = _is_non_baseline_strategy(candidate.get("strategy")) and _candidate_within_main_negative_tolerance(
                            candidate,
                            relaxed_selection_policy,
                        )
                        best_competitive_non_baseline = _is_non_baseline_strategy(profile_best.get("strategy")) and _candidate_within_main_negative_tolerance(
                            profile_best,
                            relaxed_selection_policy,
                        )
                        if candidate_competitive_non_baseline and not best_competitive_non_baseline:
                            profile_best = candidate
                            continue
                        if best_competitive_non_baseline and not candidate_competitive_non_baseline:
                            continue

                    if prefer_model_tol > 0:
                        mae_tol = max(best_mae, 1e-8) * prefer_model_tol
                        if (
                            abs(candidate_mae - best_mae) <= mae_tol
                            and _direct_strategy_model_weight(candidate["strategy"]) > _direct_strategy_model_weight(profile_best["strategy"])
                        ):
                            profile_best = candidate
                    elif bool(relaxed_selection_policy.get("enabled", False)):
                        if _direct_strategy_model_weight(candidate["strategy"]) > _direct_strategy_model_weight(profile_best["strategy"]):
                            profile_best = candidate
                    elif _direct_strategy_model_weight(candidate["strategy"]) < _direct_strategy_model_weight(profile_best["strategy"]):
                        profile_best = candidate

        relaxed_selection_payload: Dict[str, Any] = {
            "policy": dict(relaxed_selection_policy),
            "applied": False,
            "reason": "disabled_or_non_main",
            "eligible_non_baseline_candidates": 0,
            "selected_strategy_type_before": str(dict(profile_best.get("strategy", {}) or {}).get("type", "unknown")),
            "selected_strategy_type_after": str(dict(profile_best.get("strategy", {}) or {}).get("type", "unknown")),
        }
        if bool(relaxed_selection_policy.get("enabled", False)):
            eligible_non_baseline = [
                item
                for item in selection_pool
                if _is_non_baseline_strategy(item.get("strategy"))
                and _candidate_within_main_negative_tolerance(item, relaxed_selection_policy)
            ]
            relaxed_selection_payload["eligible_non_baseline_candidates"] = int(len(eligible_non_baseline))
            if not eligible_non_baseline:
                relaxed_selection_payload["reason"] = "no_non_baseline_candidate_within_tolerance"
            elif _is_non_baseline_strategy(profile_best.get("strategy")):
                relaxed_selection_payload["reason"] = "best_already_non_baseline"
            else:
                promoted_candidate = min(
                    eligible_non_baseline,
                    key=lambda item: (
                        _candidate_ranking_mae(item),
                        -_direct_strategy_model_weight(dict(item.get("strategy", {}) or {})),
                    ),
                )
                profile_best = promoted_candidate
                relaxed_selection_payload["applied"] = True
                relaxed_selection_payload["reason"] = "promoted_non_baseline_within_negative_delta_tolerance"
                relaxed_selection_payload["selected_strategy_type_after"] = str(
                    dict(profile_best.get("strategy", {}) or {}).get("type", "unknown")
                )

        selected = dict(profile_best["strategy"])
        selected["validation_mae"] = float(profile_best["validation_mae"])
        selected["stabilization_effective_mae"] = _candidate_ranking_mae(profile_best)
        selected["stabilization_overfit_penalty_ratio"] = float(
            profile_best.get("stabilization_overfit_penalty_ratio", 0.0)
        )
        selected["stabilization_aggressiveness"] = float(profile_best.get("stabilization_aggressiveness", 0.0))
        selected["composition_profile"] = profile_key
        selected["selection_pool"] = selection_pool_name
        selected["robustness_metrics"] = dict(profile_best["robustness_metrics"])
        selected["robustness_gate_pass"] = bool(profile_best["robustness_gate_pass"])
        selected["robustness_gate_reasons"] = list(profile_best["robustness_gate_reasons"])
        selected["delta_vs_persistence"] = float(profile_best.get("delta_vs_persistence", 0.0))
        selected["recent_delta_vs_persistence"] = float(profile_best.get("recent_delta_vs_persistence", 0.0))
        selected["relative_delta_vs_persistence"] = float(profile_best.get("relative_delta_vs_persistence", 0.0))
        selected["relative_recent_delta_vs_persistence"] = float(
            profile_best.get("relative_recent_delta_vs_persistence", 0.0)
        )
        selected["recent_mae"] = float(profile_best.get("recent_mae", selected["validation_mae"]))
        relaxed_candidate_rel_delta = _try_float(selected.get("relative_delta_vs_persistence"))
        relaxed_selection_payload["candidate_relative_delta_vs_persistence"] = relaxed_candidate_rel_delta
        relaxed_selection_payload["within_negative_delta_tolerance"] = bool(
            relaxed_selection_policy.get("enabled", False)
            and relaxed_candidate_rel_delta is not None
            and relaxed_candidate_rel_delta >= -float(relaxed_selection_policy.get("negative_delta_tolerance", 0.0))
        )
        selected["main_selection_relaxed_rule"] = dict(relaxed_selection_payload)
        selected["main_selection_relaxed_rule_applied"] = bool(relaxed_selection_payload.get("applied", False))

        profile_results[profile_key] = {
            "profile": profile_key,
            "config": dict(strategy_cfg),
            "stabilization_config_overrides": dict(stabilization_cfg_overrides),
            "validation_mae": float(profile_best["validation_mae"]),
            "status": "candidate",
            "selected": selected,
            "candidate_count": len(candidates),
            "robust_candidate_count": len(robust_candidates),
            "robustness_gate_pass": bool(profile_best["robustness_gate_pass"]),
            "robustness_gate_reasons": list(profile_best["robustness_gate_reasons"]),
            "robustness_metrics": dict(profile_best["robustness_metrics"]),
            "candidate_evaluations": candidate_evaluations,
            "main_selection_relaxed_rule": dict(relaxed_selection_payload),
            "main_selection_relaxed_rule_applied": bool(relaxed_selection_payload.get("applied", False)),
        }

    # Safety guard: never choose a strategy that is noticeably worse than persistence on validation.
    try:
        base_pers = baseline_map["persistence"]
        base_pers_price = close * (1.0 + base_pers)
        base_mae = float(np.mean(np.abs(target_price - base_pers_price)))
    except Exception:
        base_mae = np.inf

    default_result = profile_results.get("default")
    default_mae = None if default_result is None else default_result.get("validation_mae")
    evaluation_log: list[Dict[str, Any]] = []
    selectable_results: list[Dict[str, Any]] = []
    for profile_key in profile_sequence_keys:
        result = profile_results.get(profile_key)
        if not result:
            continue
        record = {
            "profile": profile_key,
            "validation_mae": result.get("validation_mae"),
            "status": result.get("status", "candidate"),
            "candidate_count": result.get("candidate_count", 0),
            "robust_candidate_count": result.get("robust_candidate_count", 0),
            "robustness_gate_pass": result.get("robustness_gate_pass"),
            "robustness_gate_reasons": result.get("robustness_gate_reasons", []),
            "robustness_metrics": result.get("robustness_metrics", {}),
            "stabilization_config_overrides": dict(result.get("stabilization_config_overrides", {}) or {}),
        }
        if result.get("selected") is not None:
            record["strategy"] = dict(result["selected"])
            record["stabilization_effective_mae"] = _try_float(
                dict(result.get("selected", {}) or {}).get("stabilization_effective_mae")
            )
            record["stabilization_overfit_penalty_ratio"] = _try_float(
                dict(result.get("selected", {}) or {}).get("stabilization_overfit_penalty_ratio")
            )

        main_relaxed_rule_payload = dict(result.get("main_selection_relaxed_rule", {}) or {})
        main_relaxed_policy = dict(main_relaxed_rule_payload.get("policy", {}) or {})
        candidate_rel_delta = _try_float(dict(result.get("selected", {}) or {}).get("relative_delta_vs_persistence"))
        within_main_negative_tolerance = bool(
            main_relaxed_policy.get("enabled", False)
            and candidate_rel_delta is not None
            and candidate_rel_delta >= -float(main_relaxed_policy.get("negative_delta_tolerance", 0.0))
        )
        main_relaxed_rule_payload["candidate_relative_delta_vs_persistence"] = candidate_rel_delta
        main_relaxed_rule_payload["within_negative_delta_tolerance"] = within_main_negative_tolerance

        if profile_key == "default" and result.get("validation_mae") is not None:
            result["status"] = "default_candidate"
        elif result.get("selected") is not None and default_mae is not None:
            mae_val = float(result["validation_mae"])
            delta_mae = float(default_mae - mae_val)
            rel_improvement = delta_mae / max(abs(default_mae), 1e-8)
            cfg = result["config"]
            min_improvement = float(cfg.get("profile_min_relative_improvement_vs_default", 0.0))
            disable_gap = float(cfg.get("profile_disable_relative_gap_vs_default", 0.0))
            result["delta_mae_vs_default"] = delta_mae
            result["relative_improvement_vs_default"] = rel_improvement
            record["delta_mae_vs_default"] = delta_mae
            record["relative_improvement_vs_default"] = rel_improvement
            if mae_val > default_mae * (1.0 + disable_gap) + 1e-12:
                if within_main_negative_tolerance:
                    result["status"] = "eligible_relaxed_negative_delta_tolerance"
                    main_relaxed_rule_payload["applied"] = True
                    main_relaxed_rule_payload["reason"] = "override_inactive_default_dominates"
                else:
                    result["status"] = "inactive_default_dominates"
            elif rel_improvement <= min_improvement + 1e-12:
                if within_main_negative_tolerance:
                    result["status"] = "eligible_relaxed_negative_delta_tolerance"
                    main_relaxed_rule_payload["applied"] = True
                    main_relaxed_rule_payload["reason"] = "override_fallback_default"
                else:
                    result["status"] = "fallback_default"
            else:
                result["status"] = "eligible"

        if result.get("selected") is not None:
            result["selected"]["main_selection_relaxed_rule"] = dict(main_relaxed_rule_payload)
            result["selected"]["main_selection_relaxed_rule_applied"] = bool(
                main_relaxed_rule_payload.get("applied", False)
            )
            result["main_selection_relaxed_rule"] = dict(main_relaxed_rule_payload)
            result["main_selection_relaxed_rule_applied"] = bool(main_relaxed_rule_payload.get("applied", False))

        if (
            profile_key != "default"
            and result.get("selected") is not None
            and DIRECT_STRATEGY_ROBUSTNESS_ENABLED
            and DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT
            and not bool(result.get("robustness_gate_pass", False))
        ):
            result["status"] = "fallback_default_not_robust"

        record["status"] = result.get("status", record["status"])
        record["main_selection_relaxed_rule_applied"] = bool(
            result.get("main_selection_relaxed_rule_applied", False)
        )
        record["main_selection_relaxed_rule_reason"] = str(
            dict(result.get("main_selection_relaxed_rule", {}) or {}).get("reason", "")
        )
        evaluation_log.append(record)

        if result.get("selected") is None:
            continue
        if profile_key == "default":
            selectable_results.append(result)
        elif result.get("status") in {"eligible", "eligible_relaxed_negative_delta_tolerance"}:
            if not (DIRECT_STRATEGY_ROBUSTNESS_ENABLED and DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT):
                selectable_results.append(result)
            elif bool(result.get("robustness_gate_pass", False)):
                selectable_results.append(result)

    if not selectable_results:
        return {
            "type": "model_only",
            "alpha": 1.0,
            "baseline": "persistence",
            "direction_calibration": direction_calibration,
            "main_selection_relaxed_rule": {
                "policy": _main_relaxed_selection_policy(active_profile_key),
                "applied": False,
                "reason": "no_selectable_results",
            },
            "main_selection_relaxed_rule_applied": False,
            "main_selection_final_ranking_reason": "no_selectable_results",
            "main_selection_baseline_overridden": False,
            "main_selection_candidate_type": "model_only",
            "main_persistence_promotion_applied": False,
            "main_persistence_promotable_candidate_count": 0,
            "main_persistence_promotable_non_baseline_count": 0,
            "main_persistence_baseline_excluded_from_promotion": False,
            "main_persistence_promotion": {
                "policy": dict(promotion_policy),
                "applied": False,
                "reason": "no_selectable_results",
                "promotable_candidates": 0,
                "promotable_non_baseline_candidates": 0,
                "baseline_excluded_from_promotion": False,
            },
            **_stabilization_strategy_fields(stabilization_context),
        }

    best_result = selectable_results[0]
    main_final_ranking_policy = _main_relaxed_selection_policy(active_profile_key)
    main_final_ranking_reason = "initial_candidate"
    main_baseline_overridden = False
    for candidate in selectable_results[1:]:
        candidate_mae = _profile_result_ranking_mae(candidate)
        best_mae = _profile_result_ranking_mae(best_result)
        mae_close_tol = max(max(best_mae, candidate_mae), 1e-8) * float(DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO)
        if candidate_mae < best_mae - 1e-12:
            best_result = candidate
            main_final_ranking_reason = "mae_better"
            continue
        if abs(candidate_mae - best_mae) <= max(mae_close_tol, 1e-12):
            candidate_rob = _robustness_comparison_key(dict(candidate.get("robustness_metrics", {}) or {}))
            best_rob = _robustness_comparison_key(dict(best_result.get("robustness_metrics", {}) or {}))
            if candidate_rob > best_rob:
                best_result = candidate
                main_final_ranking_reason = "robustness_better_on_near_tie"
                continue
            if candidate_rob == best_rob and bool(main_final_ranking_policy.get("enabled", False)):
                candidate_competitive_non_baseline = _is_non_baseline_strategy(dict(candidate.get("selected", {}) or {})) and _candidate_within_main_negative_tolerance(
                    dict(candidate.get("selected", {}) or {}),
                    main_final_ranking_policy,
                )
                best_competitive_non_baseline = _is_non_baseline_strategy(dict(best_result.get("selected", {}) or {})) and _candidate_within_main_negative_tolerance(
                    dict(best_result.get("selected", {}) or {}),
                    main_final_ranking_policy,
                )
                if candidate_competitive_non_baseline and not best_competitive_non_baseline:
                    previous_type = str(dict(best_result.get("selected", {}) or {}).get("type", "unknown"))
                    best_result = candidate
                    if previous_type == "baseline_only" and _is_non_baseline_strategy(dict(best_result.get("selected", {}) or {})):
                        main_baseline_overridden = True
                    main_final_ranking_reason = "near_tie_prefer_non_baseline_main"
                    continue
                if best_competitive_non_baseline and not candidate_competitive_non_baseline:
                    continue

            candidate_weight = _direct_strategy_model_weight(candidate["selected"])
            best_weight = _direct_strategy_model_weight(best_result["selected"])
            if bool(main_final_ranking_policy.get("enabled", False)):
                if candidate_weight > best_weight:
                    previous_type = str(dict(best_result.get("selected", {}) or {}).get("type", "unknown"))
                    best_result = candidate
                    if previous_type == "baseline_only" and _is_non_baseline_strategy(dict(best_result.get("selected", {}) or {})):
                        main_baseline_overridden = True
                    main_final_ranking_reason = "near_tie_prefer_higher_model_weight_main"
            elif candidate_weight < best_weight:
                best_result = candidate
                main_final_ranking_reason = "near_tie_prefer_lower_model_weight_default"

    final_relaxed_policy = dict(main_final_ranking_policy)
    final_relaxed_payload: Dict[str, Any] = {
        "policy": dict(final_relaxed_policy),
        "applied": False,
        "reason": "disabled_or_non_main",
        "selected_profile_before": str(best_result.get("profile", "unknown")),
        "selected_profile_after": str(best_result.get("profile", "unknown")),
        "selected_strategy_type_before": str(dict(best_result.get("selected", {}) or {}).get("type", "unknown")),
        "selected_strategy_type_after": str(dict(best_result.get("selected", {}) or {}).get("type", "unknown")),
        "eligible_main_candidates": 0,
    }
    if bool(final_relaxed_policy.get("enabled", False)):
        tolerance = float(final_relaxed_policy.get("negative_delta_tolerance", 0.0))
        best_mae = _profile_result_ranking_mae(best_result)
        mae_limit = best_mae * (1.0 + tolerance) + 1e-12
        eligible_main_candidates = [
            item
            for item in selectable_results
            if str(item.get("profile")) == "main_direct_pipeline"
            and _is_non_baseline_strategy(dict(item.get("selected", {}) or {}))
            and _candidate_within_main_negative_tolerance(dict(item.get("selected", {}) or {}), final_relaxed_policy)
            and _profile_result_ranking_mae(item) <= mae_limit
        ]
        final_relaxed_payload["eligible_main_candidates"] = int(len(eligible_main_candidates))
        if not eligible_main_candidates:
            final_relaxed_payload["reason"] = "no_main_candidate_within_tolerance"
        else:
            promoted_result = min(
                eligible_main_candidates,
                key=lambda item: (
                    _profile_result_ranking_mae(item),
                    -_direct_strategy_model_weight(dict(item.get("selected", {}) or {})),
                ),
            )
            same_as_current = (
                str(promoted_result.get("profile")) == str(best_result.get("profile"))
                and str(dict(promoted_result.get("selected", {}) or {}).get("type"))
                == str(dict(best_result.get("selected", {}) or {}).get("type"))
                and str(dict(promoted_result.get("selected", {}) or {}).get("baseline"))
                == str(dict(best_result.get("selected", {}) or {}).get("baseline"))
                and abs(
                    float(dict(promoted_result.get("selected", {}) or {}).get("alpha", 1.0))
                    - float(dict(best_result.get("selected", {}) or {}).get("alpha", 1.0))
                )
                <= 1e-12
            )
            if same_as_current:
                final_relaxed_payload["reason"] = "best_already_main_non_baseline"
            else:
                previous_type = str(dict(best_result.get("selected", {}) or {}).get("type", "unknown"))
                best_result = promoted_result
                final_relaxed_payload["applied"] = True
                final_relaxed_payload["reason"] = "promoted_main_non_baseline_within_negative_delta_tolerance"
                final_relaxed_payload["selected_profile_after"] = str(best_result.get("profile", "unknown"))
                final_relaxed_payload["selected_strategy_type_after"] = str(
                    dict(best_result.get("selected", {}) or {}).get("type", "unknown")
                )
                if previous_type == "baseline_only" and _is_non_baseline_strategy(dict(best_result.get("selected", {}) or {})):
                    main_baseline_overridden = True
                main_final_ranking_reason = "main_relaxed_non_baseline_promotion"

    promotion_payload: Dict[str, Any] = {
        "policy": dict(promotion_policy),
        "applied": False,
        "reason": "disabled_or_non_main",
        "selected_profile_before": str(best_result.get("profile", "unknown")),
        "selected_profile_after": str(best_result.get("profile", "unknown")),
        "selected_delta_vs_persistence_before": float(dict(best_result.get("selected", {}) or {}).get("delta_vs_persistence", 0.0)),
        "selected_recent_delta_vs_persistence_before": float(dict(best_result.get("selected", {}) or {}).get("recent_delta_vs_persistence", 0.0)),
        "selected_delta_vs_persistence_after": float(dict(best_result.get("selected", {}) or {}).get("delta_vs_persistence", 0.0)),
        "selected_recent_delta_vs_persistence_after": float(dict(best_result.get("selected", {}) or {}).get("recent_delta_vs_persistence", 0.0)),
        "promotable_candidates": 0,
        "promotable_non_baseline_candidates": 0,
        "baseline_excluded_from_promotion": False,
    }
    if bool(promotion_policy.get("enabled", False)):
        exclude_baseline_from_promotion = str(promotion_policy.get("active_profile", "")) == "main_direct_pipeline"
        promotion_payload["baseline_excluded_from_promotion"] = bool(exclude_baseline_from_promotion)
        if bool(final_relaxed_payload.get("applied", False)) and _is_non_baseline_strategy(
            dict(best_result.get("selected", {}) or {})
        ):
            promotion_payload["reason"] = "skipped_due_main_relaxed_selection"
        else:
            best_mae = _profile_result_ranking_mae(best_result)
            mae_limit = best_mae * (1.0 + float(promotion_policy["mae_tolerance_ratio"])) + 1e-12
            min_delta = float(promotion_policy["min_delta_vs_persistence"])
            min_recent_delta = float(promotion_policy["min_recent_delta_vs_persistence"])
            promotion_max_penalty = float(
                dict(stabilization_context.get("policy", {}) or {}).get("promotion_max_penalty_ratio", 1.0)
            )

            promotable: list[Dict[str, Any]] = []
            for profile_candidate in selectable_results:
                profile_key = str(profile_candidate.get("profile", "unknown"))
                candidate_pool = list(profile_candidate.get("candidate_evaluations", []) or [])
                if not candidate_pool and profile_candidate.get("selected") is not None:
                    candidate_pool = [
                        {
                            "strategy": dict(profile_candidate.get("selected", {}) or {}),
                            "validation_mae": float(profile_candidate.get("validation_mae", np.inf)),
                            "stabilization_effective_mae": _profile_result_ranking_mae(profile_candidate),
                            "stabilization_overfit_penalty_ratio": float(
                                dict(profile_candidate.get("selected", {}) or {}).get(
                                    "stabilization_overfit_penalty_ratio",
                                    0.0,
                                )
                            ),
                            "robustness_metrics": dict(profile_candidate.get("robustness_metrics", {}) or {}),
                            "robustness_gate_pass": bool(profile_candidate.get("robustness_gate_pass", True)),
                            "robustness_gate_reasons": list(profile_candidate.get("robustness_gate_reasons", []) or []),
                            "delta_vs_persistence": dict(profile_candidate.get("selected", {}) or {}).get("delta_vs_persistence"),
                            "recent_delta_vs_persistence": dict(profile_candidate.get("selected", {}) or {}).get("recent_delta_vs_persistence"),
                            "recent_mae": dict(profile_candidate.get("selected", {}) or {}).get("recent_mae"),
                        }
                    ]

                for candidate_eval in candidate_pool:
                    candidate_mae = float(candidate_eval.get("validation_mae", np.inf))
                    candidate_effective_mae = _candidate_ranking_mae(candidate_eval)
                    candidate_penalty = float(candidate_eval.get("stabilization_overfit_penalty_ratio", 0.0) or 0.0)
                    delta_vs_persistence = candidate_eval.get("delta_vs_persistence")
                    recent_delta_vs_persistence = candidate_eval.get("recent_delta_vs_persistence")
                    strategy_eval = dict(candidate_eval.get("strategy", {}) or {})
                    if exclude_baseline_from_promotion and not _is_non_baseline_strategy(strategy_eval):
                        continue
                    if delta_vs_persistence is None or recent_delta_vs_persistence is None:
                        continue
                    if candidate_effective_mae > mae_limit:
                        continue
                    if bool(stabilization_context.get("enabled", False)) and candidate_penalty > promotion_max_penalty:
                        continue
                    if float(delta_vs_persistence) < min_delta:
                        continue
                    if float(recent_delta_vs_persistence) < min_recent_delta:
                        continue

                    selected_candidate = dict(candidate_eval.get("strategy", {}) or {})
                    selected_candidate["validation_mae"] = candidate_mae
                    selected_candidate["stabilization_effective_mae"] = candidate_effective_mae
                    selected_candidate["stabilization_overfit_penalty_ratio"] = candidate_penalty
                    selected_candidate["composition_profile"] = profile_key
                    selected_candidate["selection_pool"] = "main_persistence_promotion"
                    selected_candidate["robustness_metrics"] = dict(candidate_eval.get("robustness_metrics", {}) or {})
                    selected_candidate["robustness_gate_pass"] = bool(candidate_eval.get("robustness_gate_pass", True))
                    selected_candidate["robustness_gate_reasons"] = list(candidate_eval.get("robustness_gate_reasons", []) or [])
                    selected_candidate["delta_vs_persistence"] = float(delta_vs_persistence)
                    selected_candidate["recent_delta_vs_persistence"] = float(recent_delta_vs_persistence)
                    selected_candidate["recent_mae"] = float(candidate_eval.get("recent_mae", candidate_mae))

                    promotable.append(
                        {
                            "profile_result": profile_candidate,
                            "profile": profile_key,
                            "selected": selected_candidate,
                            "validation_mae": candidate_mae,
                            "stabilization_effective_mae": candidate_effective_mae,
                            "stabilization_overfit_penalty_ratio": candidate_penalty,
                            "robustness_metrics": dict(candidate_eval.get("robustness_metrics", {}) or {}),
                        }
                    )

            promotion_payload["promotable_candidates"] = int(len(promotable))
            promotion_payload["promotable_non_baseline_candidates"] = int(
                sum(1 for item in promotable if _is_non_baseline_strategy(dict(item.get("selected", {}) or {})))
            )
            if promotable:
                promoted_result = max(
                    promotable,
                    key=lambda item: (
                        float(dict(item.get("selected", {}) or {}).get("recent_delta_vs_persistence", float("-inf"))),
                        float(dict(item.get("selected", {}) or {}).get("delta_vs_persistence", float("-inf"))),
                        -float(item.get("stabilization_overfit_penalty_ratio", 0.0)),
                        -float(item.get("stabilization_effective_mae", np.inf)),
                        _robustness_comparison_key(dict(item.get("robustness_metrics", {}) or {})),
                    ),
                )
                same_as_current = (
                    str(promoted_result.get("profile")) == str(best_result.get("profile"))
                    and str(dict(promoted_result.get("selected", {}) or {}).get("type")) == str(dict(best_result.get("selected", {}) or {}).get("type"))
                    and str(dict(promoted_result.get("selected", {}) or {}).get("baseline")) == str(dict(best_result.get("selected", {}) or {}).get("baseline"))
                    and abs(float(dict(promoted_result.get("selected", {}) or {}).get("alpha", 1.0)) - float(dict(best_result.get("selected", {}) or {}).get("alpha", 1.0))) <= 1e-12
                )
                if same_as_current:
                    promotion_payload["reason"] = "best_already_promotable"
                else:
                    previous_type = str(dict(best_result.get("selected", {}) or {}).get("type", "unknown"))
                    base_profile_result = dict(promoted_result.get("profile_result", {}) or {})
                    best_result = dict(base_profile_result)
                    best_result["selected"] = dict(promoted_result.get("selected", {}) or {})
                    best_result["validation_mae"] = float(promoted_result.get("validation_mae", np.inf))
                    best_result["robustness_metrics"] = dict(promoted_result.get("robustness_metrics", {}) or {})
                    promotion_payload["applied"] = True
                    promotion_payload["reason"] = "promoted_by_recent_delta_vs_persistence"
                    promotion_payload["selected_profile_after"] = str(best_result.get("profile", "unknown"))
                    promotion_payload["selected_delta_vs_persistence_after"] = float(
                        dict(best_result.get("selected", {}) or {}).get("delta_vs_persistence", 0.0)
                    )
                    promotion_payload["selected_recent_delta_vs_persistence_after"] = float(
                        dict(best_result.get("selected", {}) or {}).get("recent_delta_vs_persistence", 0.0)
                    )
                    if previous_type == "baseline_only" and _is_non_baseline_strategy(dict(best_result.get("selected", {}) or {})):
                        main_baseline_overridden = True
                    main_final_ranking_reason = "main_persistence_promotion"
            else:
                promotion_payload["reason"] = "no_promotable_candidate"

    best = dict(best_result["selected"])
    best["profile_selection_mode"] = "validation_plus_multi_window_robustness"
    best["profile_evaluations"] = evaluation_log
    best["default_validation_mae"] = default_mae
    best["selected_profile_status"] = best_result.get("status", "eligible")
    best["selected_profile_robustness"] = dict(best_result.get("robustness_metrics", {}) or {})
    best["main_selection_relaxed_rule"] = final_relaxed_payload
    best["main_selection_relaxed_rule_applied"] = bool(final_relaxed_payload.get("applied", False))
    best["main_selection_final_ranking_reason"] = str(main_final_ranking_reason)
    best["main_selection_baseline_overridden"] = bool(main_baseline_overridden)
    best["main_selection_candidate_type"] = str(best.get("type", "model_only"))
    best["main_persistence_promotion_applied"] = bool(promotion_payload.get("applied", False))
    best["main_persistence_promotable_candidate_count"] = int(promotion_payload.get("promotable_candidates", 0) or 0)
    best["main_persistence_promotable_non_baseline_count"] = int(
        promotion_payload.get("promotable_non_baseline_candidates", 0) or 0
    )
    best["main_persistence_baseline_excluded_from_promotion"] = bool(
        promotion_payload.get("baseline_excluded_from_promotion", False)
    )
    best["main_persistence_promotion"] = promotion_payload
    best_cfg = dict(best_result["config"])
    best_profile = best_result["profile"]
    best_mae = float(best_result["validation_mae"])

    safe_strategy = _apply_persistence_guard(
        base_mae=base_mae,
        best_mae=best_mae,
        best_cfg=best_cfg,
        best_profile=best_profile,
        best_result=best_result,
        close=close,
        target_price=target_price,
        target_return=target_return,
        baseline_map=baseline_map,
        evaluation_log=evaluation_log,
        default_mae=default_mae,
        direct_model=direct_model,
    )
    if safe_strategy is not None:
        safe_strategy["direction_calibration"] = direction_calibration
        safe_strategy["main_selection_relaxed_rule"] = final_relaxed_payload
        safe_strategy["main_selection_relaxed_rule_applied"] = bool(final_relaxed_payload.get("applied", False))
        safe_strategy["main_selection_final_ranking_reason"] = "persistence_guard_fallback"
        safe_strategy["main_selection_baseline_overridden"] = bool(main_baseline_overridden)
        safe_strategy["main_selection_candidate_type"] = str(safe_strategy.get("type", "baseline_only"))
        safe_strategy["main_persistence_promotion_applied"] = bool(promotion_payload.get("applied", False))
        safe_strategy["main_persistence_promotable_candidate_count"] = int(
            promotion_payload.get("promotable_candidates", 0) or 0
        )
        safe_strategy["main_persistence_promotable_non_baseline_count"] = int(
            promotion_payload.get("promotable_non_baseline_candidates", 0) or 0
        )
        safe_strategy["main_persistence_baseline_excluded_from_promotion"] = bool(
            promotion_payload.get("baseline_excluded_from_promotion", False)
        )
        safe_strategy["main_persistence_promotion"] = promotion_payload
        safe_strategy.update(_stabilization_strategy_fields(stabilization_context))
        return safe_strategy

    direct_model.composition_profile = best_profile
    if best_cfg is not None:
        direct_model.composition_config = dict(best_cfg)
    best["direction_calibration"] = direction_calibration
    best.update(_stabilization_strategy_fields(stabilization_context))
    return best
