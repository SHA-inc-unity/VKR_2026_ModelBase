from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    MAIN_HOLDOUT_SAFEGUARD_ENABLED,
    MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE,
    MAIN_HOLDOUT_SAFEGUARD_MIN_RELATIVE_IMPROVEMENT_TO_KEEP,
    MAIN_HOLDOUT_SAFEGUARD_MIN_POINTS,
    MAIN_HOLDOUT_SAFEGUARD_MODEL_KEY,
    MAIN_HOLDOUT_SAFEGUARD_PREFER_PERSISTENCE_ON_NEAR_TIE,
    MAIN_HOLDOUT_SAFEGUARD_TRIGGER_ON_ANY_NEGATIVE_DELTA,
)
from catboost_floader.evaluation.backtest import build_direct_baselines

from catboost_floader.core.utils import _drop_non_model_columns
from catboost_floader.selection.direct_robustness import _safe_float


def _extract_snapshot_metrics(backtest_summary: Dict[str, Any] | None) -> Dict[str, Any]:
    summary = dict(backtest_summary or {})
    direct_mae = _safe_float(dict(summary.get("direct_model", {}) or {}).get("MAE"))
    persistence_mae = _safe_float(
        dict(dict(summary.get("direct_baselines", {}) or {}).get("persistence", {}) or {}).get("MAE")
    )
    if direct_mae is None or persistence_mae is None:
        return {
            "snapshot_delta_vs_baseline": None,
            "snapshot_relative_delta_vs_baseline": None,
            "snapshot_direct_mae": direct_mae,
            "snapshot_persistence_mae": persistence_mae,
        }
    delta = float(persistence_mae - direct_mae)
    rel_delta = float(delta / max(abs(persistence_mae), 1e-8))
    return {
        "snapshot_delta_vs_baseline": delta,
        "snapshot_relative_delta_vs_baseline": rel_delta,
        "snapshot_direct_mae": float(direct_mae),
        "snapshot_persistence_mae": float(persistence_mae),
    }


def _strategy_descriptor(strategy: Dict[str, Any] | None) -> Dict[str, Any]:
    strategy = dict(strategy or {})
    relaxed_payload = dict(strategy.get("main_selection_relaxed_rule", {}) or {})
    return {
        "type": str(strategy.get("type", "model_only")),
        "alpha": _safe_float(strategy.get("alpha")),
        "baseline": str(strategy.get("baseline", "persistence")),
        "validation_mae": _safe_float(strategy.get("validation_mae")),
        "composition_profile": strategy.get("composition_profile"),
        "selection_pool": strategy.get("selection_pool"),
        "profile_selection_mode": strategy.get("profile_selection_mode"),
        "main_selection_relaxed_rule_applied": bool(strategy.get("main_selection_relaxed_rule_applied", False)),
        "main_selection_relaxed_rule_reason": str(relaxed_payload.get("reason", "")),
        "main_selection_final_ranking_reason": str(strategy.get("main_selection_final_ranking_reason", "")),
        "main_selection_baseline_overridden": bool(strategy.get("main_selection_baseline_overridden", False)),
        "main_selection_candidate_type": str(strategy.get("main_selection_candidate_type", strategy.get("type", "model_only"))),
    }


def _main_holdout_safeguard_policy() -> Dict[str, Any]:
    return {
        "enabled": bool(MAIN_HOLDOUT_SAFEGUARD_ENABLED),
        "model_key": str(MAIN_HOLDOUT_SAFEGUARD_MODEL_KEY),
        "min_points": int(MAIN_HOLDOUT_SAFEGUARD_MIN_POINTS),
        "max_relative_underperformance": float(MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE),
        "trigger_on_any_negative_delta": bool(MAIN_HOLDOUT_SAFEGUARD_TRIGGER_ON_ANY_NEGATIVE_DELTA),
        "prefer_persistence_on_near_tie": bool(MAIN_HOLDOUT_SAFEGUARD_PREFER_PERSISTENCE_ON_NEAR_TIE),
        "min_relative_improvement_to_keep": float(MAIN_HOLDOUT_SAFEGUARD_MIN_RELATIVE_IMPROVEMENT_TO_KEEP),
    }


def _resolve_baseline_return(
    baselines: pd.DataFrame,
    column_name: str,
    expected_len: int,
) -> np.ndarray:
    if column_name not in baselines.columns:
        return np.zeros(expected_len, dtype=float)
    return pd.to_numeric(baselines[column_name], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def _compose_strategy_return(
    raw_pred_return: np.ndarray,
    baseline_map: Dict[str, np.ndarray],
    strategy_candidate: Dict[str, Any] | None,
) -> np.ndarray:
    strategy = dict(strategy_candidate or {})
    strategy_type = str(strategy.get("type", "model_only"))
    baseline_name = str(strategy.get("baseline", "persistence"))
    baseline_return = baseline_map.get(baseline_name, baseline_map["persistence"])

    if strategy_type == "baseline_only":
        return np.asarray(baseline_return, dtype=float)
    if strategy_type == "blend":
        alpha = float(strategy.get("alpha", 1.0))
        return alpha * np.asarray(raw_pred_return, dtype=float) + (1.0 - alpha) * np.asarray(baseline_return, dtype=float)
    return np.asarray(raw_pred_return, dtype=float)


def _evaluate_holdout_vs_persistence(
    direct_model,
    X_holdout_full: pd.DataFrame,
    y_holdout: pd.DataFrame,
    strategy_candidate: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    candidate_descriptor = _strategy_descriptor(strategy_candidate or getattr(direct_model, "strategy", {}))

    if X_holdout_full.empty or y_holdout.empty:
        return {
            "evaluated": False,
            "row_count": int(len(X_holdout_full)),
            "strategy_mae": None,
            "persistence_mae": None,
            "delta_vs_persistence": None,
            "relative_underperformance_vs_persistence": None,
            "relative_improvement_vs_persistence": None,
            "candidate_descriptor": candidate_descriptor,
        }

    X_model = _drop_non_model_columns(X_holdout_full)
    X_aligned = X_model.reindex(columns=direct_model.feature_names, fill_value=0.0)
    close_series = X_holdout_full.get("close")
    target_series = y_holdout.get("target_future_close")
    if close_series is None or target_series is None:
        return {
            "evaluated": False,
            "row_count": int(len(X_holdout_full)),
            "strategy_mae": None,
            "persistence_mae": None,
            "delta_vs_persistence": None,
            "relative_underperformance_vs_persistence": None,
            "relative_improvement_vs_persistence": None,
            "candidate_descriptor": candidate_descriptor,
        }

    close = pd.to_numeric(close_series, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_price = pd.to_numeric(target_series, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if len(close) == 0 or len(target_price) == 0:
        return {
            "evaluated": False,
            "row_count": int(len(X_holdout_full)),
            "strategy_mae": None,
            "persistence_mae": None,
            "delta_vs_persistence": None,
            "relative_underperformance_vs_persistence": None,
            "relative_improvement_vs_persistence": None,
            "candidate_descriptor": candidate_descriptor,
        }

    try:
        raw_pred_return = np.asarray(direct_model.predict_details(X_aligned).get("raw_pred_return"), dtype=float)
    except Exception:
        raw_pred_return = np.asarray(direct_model.predict(X_aligned), dtype=float)

    baselines = build_direct_baselines(X_holdout_full)
    persistence_return = _resolve_baseline_return(baselines, "baseline_persistence_return", len(close))
    rolling_mean_return = _resolve_baseline_return(baselines, "baseline_rolling_mean_return", len(close))
    trend_return = _resolve_baseline_return(baselines, "baseline_trend_return", len(close))
    baseline_map = {
        "persistence": persistence_return,
        "rolling_mean": rolling_mean_return,
        "trend": trend_return,
    }
    pred_return = _compose_strategy_return(raw_pred_return, baseline_map, strategy_candidate)

    pred_price = close * (1.0 + pred_return)
    persistence_price = close * (1.0 + persistence_return)

    if len(pred_price) != len(target_price) or len(persistence_price) != len(target_price):
        min_len = min(len(pred_price), len(persistence_price), len(target_price))
        pred_price = pred_price[:min_len]
        persistence_price = persistence_price[:min_len]
        target_price = target_price[:min_len]

    if len(target_price) == 0:
        return {
            "evaluated": False,
            "row_count": 0,
            "strategy_mae": None,
            "persistence_mae": None,
            "delta_vs_persistence": None,
            "relative_underperformance_vs_persistence": None,
            "relative_improvement_vs_persistence": None,
            "candidate_descriptor": candidate_descriptor,
        }

    strategy_mae = float(np.mean(np.abs(target_price - pred_price)))
    persistence_mae = float(np.mean(np.abs(target_price - persistence_price)))
    delta = float(persistence_mae - strategy_mae)
    relative_underperformance = float(max(0.0, strategy_mae - persistence_mae) / max(abs(persistence_mae), 1e-8))
    relative_improvement = float(max(0.0, persistence_mae - strategy_mae) / max(abs(persistence_mae), 1e-8))

    return {
        "evaluated": True,
        "row_count": int(len(target_price)),
        "strategy_mae": strategy_mae,
        "persistence_mae": persistence_mae,
        "delta_vs_persistence": delta,
        "relative_underperformance_vs_persistence": relative_underperformance,
        "relative_improvement_vs_persistence": relative_improvement,
        "candidate_descriptor": candidate_descriptor,
    }


def _apply_main_holdout_safeguard(
    *,
    model_key: str,
    direct_model,
    direct_strategy: Dict[str, Any],
    X_holdout_full: pd.DataFrame,
    y_holdout: pd.DataFrame,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    policy = _main_holdout_safeguard_policy()
    strategy_before_guard = dict(direct_strategy or {})
    evaluation_before_guard = _evaluate_holdout_vs_persistence(
        direct_model,
        X_holdout_full,
        y_holdout,
        strategy_candidate=strategy_before_guard,
    )

    payload = {
        "policy": policy,
        "evaluated": bool(evaluation_before_guard.get("evaluated", False)),
        "applied": False,
        "reason": "not_applicable",
        "row_count": int(evaluation_before_guard.get("row_count", 0) or 0),
        "strategy_mae": _safe_float(evaluation_before_guard.get("strategy_mae")),
        "persistence_mae": _safe_float(evaluation_before_guard.get("persistence_mae")),
        "delta_vs_persistence": _safe_float(evaluation_before_guard.get("delta_vs_persistence")),
        "relative_underperformance_vs_persistence": _safe_float(evaluation_before_guard.get("relative_underperformance_vs_persistence")),
        "relative_improvement_vs_persistence": _safe_float(evaluation_before_guard.get("relative_improvement_vs_persistence")),
        "final_holdout_candidate_before_guard": dict(evaluation_before_guard.get("candidate_descriptor", {}) or {}),
        "final_holdout_candidate_after_guard": dict(evaluation_before_guard.get("candidate_descriptor", {}) or {}),
        "final_holdout_candidate_before_guard_delta_vs_baseline": _safe_float(evaluation_before_guard.get("delta_vs_persistence")),
        "final_holdout_candidate_after_guard_delta_vs_baseline": _safe_float(evaluation_before_guard.get("delta_vs_persistence")),
        "final_holdout_delta_vs_baseline": _safe_float(evaluation_before_guard.get("delta_vs_persistence")),
        "final_holdout_guard_reason": "not_applicable",
        "final_holdout_guard_applied": False,
    }

    def _annotate_strategy(strategy_value: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(strategy_value or {})
        out["holdout_delta_vs_persistence"] = payload["delta_vs_persistence"]
        out["holdout_relative_underperformance_vs_persistence"] = payload["relative_underperformance_vs_persistence"]
        out["final_holdout_delta_vs_baseline"] = payload["final_holdout_delta_vs_baseline"]
        out["final_holdout_guard_reason"] = payload["final_holdout_guard_reason"]
        out["final_holdout_guard_applied"] = bool(payload["final_holdout_guard_applied"])
        out["final_holdout_candidate_before_guard"] = dict(payload.get("final_holdout_candidate_before_guard", {}) or {})
        out["final_holdout_candidate_after_guard"] = dict(payload.get("final_holdout_candidate_after_guard", {}) or {})
        out["final_holdout_safeguard_applied"] = bool(payload["final_holdout_guard_applied"])
        out["main_selection_candidate_type"] = str(out.get("main_selection_candidate_type", out.get("type", "model_only")))
        out["main_persistence_promotion_applied"] = bool(out.get("main_persistence_promotion_applied", False))
        out["main_persistence_promotable_candidate_count"] = int(out.get("main_persistence_promotable_candidate_count", 0) or 0)
        out["main_persistence_promotable_non_baseline_count"] = int(
            out.get("main_persistence_promotable_non_baseline_count", 0) or 0
        )
        out["main_persistence_baseline_excluded_from_promotion"] = bool(
            out.get("main_persistence_baseline_excluded_from_promotion", False)
        )
        return out

    strategy_out = _annotate_strategy(strategy_before_guard)

    if not policy["enabled"]:
        payload["reason"] = "disabled_in_config"
        payload["final_holdout_guard_reason"] = payload["reason"]
        return strategy_out, payload
    if str(model_key) != str(policy["model_key"]):
        payload["reason"] = "non_main_model"
        payload["final_holdout_guard_reason"] = payload["reason"]
        return strategy_out, payload
    if not payload["evaluated"]:
        payload["reason"] = "holdout_not_evaluable"
        payload["final_holdout_guard_reason"] = payload["reason"]
        return strategy_out, payload
    if payload["row_count"] < int(policy["min_points"]):
        payload["reason"] = "insufficient_holdout_points"
        payload["final_holdout_guard_reason"] = payload["reason"]
        return strategy_out, payload

    delta = _safe_float(payload.get("delta_vs_persistence"))
    relative_underperformance = _safe_float(payload.get("relative_underperformance_vs_persistence"))
    relative_improvement = _safe_float(payload.get("relative_improvement_vs_persistence"))

    if delta is None or relative_underperformance is None:
        payload["reason"] = "missing_holdout_metrics"
        payload["final_holdout_guard_reason"] = payload["reason"]
        return strategy_out, payload

    fallback_reason = None
    if bool(policy["trigger_on_any_negative_delta"]) and delta < 0.0:
        fallback_reason = "negative_vs_persistence"
    elif delta < 0.0 and relative_underperformance >= float(policy["max_relative_underperformance"]):
        fallback_reason = "clear_underperformance_vs_persistence"
    elif (
        bool(policy["prefer_persistence_on_near_tie"])
        and delta >= 0.0
        and relative_improvement is not None
        and relative_improvement <= float(policy["min_relative_improvement_to_keep"])
    ):
        fallback_reason = "near_tie_prefer_persistence"

    if fallback_reason is not None:
        fallback = {
            "type": "baseline_only",
            "alpha": 0.0,
            "baseline": "persistence",
            "validation_mae": strategy_out.get("validation_mae"),
            "composition_profile": strategy_out.get("composition_profile", "default"),
            "selection_pool": strategy_out.get("selection_pool", "holdout_safeguard"),
            "profile_selection_mode": strategy_out.get("profile_selection_mode", "validation_plus_multi_window_robustness"),
            "profile_evaluations": list(strategy_out.get("profile_evaluations", []) or []),
            "default_validation_mae": strategy_out.get("default_validation_mae"),
            "selected_profile_status": strategy_out.get("selected_profile_status"),
            "selected_profile_robustness": dict(strategy_out.get("selected_profile_robustness", {}) or {}),
            "robustness_metrics": dict(strategy_out.get("robustness_metrics", {}) or {}),
            "robustness_gate_pass": True,
            "robustness_gate_reasons": list(strategy_out.get("robustness_gate_reasons", []) or []) + ["main_holdout_persistence_safeguard"],
            "main_selection_relaxed_rule": dict(strategy_out.get("main_selection_relaxed_rule", {}) or {}),
            "main_selection_relaxed_rule_applied": bool(strategy_out.get("main_selection_relaxed_rule_applied", False)),
            "main_selection_final_ranking_reason": str(strategy_out.get("main_selection_final_ranking_reason", "holdout_safeguard_fallback")),
            "main_selection_baseline_overridden": bool(strategy_out.get("main_selection_baseline_overridden", False)),
            "main_selection_candidate_type": "baseline_only",
            "main_persistence_promotion_applied": bool(strategy_out.get("main_persistence_promotion_applied", False)),
            "main_persistence_promotable_candidate_count": int(
                strategy_out.get("main_persistence_promotable_candidate_count", 0) or 0
            ),
            "main_persistence_promotable_non_baseline_count": int(
                strategy_out.get("main_persistence_promotable_non_baseline_count", 0) or 0
            ),
            "main_persistence_baseline_excluded_from_promotion": bool(
                strategy_out.get("main_persistence_baseline_excluded_from_promotion", False)
            ),
            "stabilization_model_key": strategy_out.get("stabilization_model_key"),
            "stabilization_targeted_model": bool(strategy_out.get("stabilization_targeted_model", False)),
            "stabilization_applied": bool(strategy_out.get("stabilization_applied", False)),
            "stabilization_overfit_status": strategy_out.get("stabilization_overfit_status"),
            "stabilization_overfit_reason": strategy_out.get("stabilization_overfit_reason"),
            "stabilization_reference_overfit": dict(
                strategy_out.get("stabilization_reference_overfit", {}) or {}
            ),
            "stabilization_policy": dict(strategy_out.get("stabilization_policy", {}) or {}),
            "holdout_delta_vs_persistence": delta,
            "holdout_relative_underperformance_vs_persistence": relative_underperformance,
            "final_holdout_safeguard_applied": True,
        }

        evaluation_after_guard = _evaluate_holdout_vs_persistence(
            direct_model,
            X_holdout_full,
            y_holdout,
            strategy_candidate=fallback,
        )

        payload["applied"] = True
        payload["reason"] = fallback_reason
        payload["final_holdout_candidate_after_guard"] = dict(evaluation_after_guard.get("candidate_descriptor", {}) or {})
        payload["final_holdout_candidate_after_guard_delta_vs_baseline"] = _safe_float(evaluation_after_guard.get("delta_vs_persistence"))
        payload["final_holdout_delta_vs_baseline"] = _safe_float(evaluation_after_guard.get("delta_vs_persistence"))
        payload["final_holdout_guard_reason"] = payload["reason"]
        payload["final_holdout_guard_applied"] = True
        return _annotate_strategy(fallback), payload

    payload["reason"] = "not_triggered"
    payload["final_holdout_guard_reason"] = payload["reason"]
    payload["final_holdout_guard_applied"] = False
    return _annotate_strategy(strategy_out), payload
