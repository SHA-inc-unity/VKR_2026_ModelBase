from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    MAIN_HOLDOUT_SAFEGUARD_ENABLED,
    MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE,
    MAIN_HOLDOUT_SAFEGUARD_MIN_POINTS,
    MAIN_HOLDOUT_SAFEGUARD_MODEL_KEY,
)
from catboost_floader.evaluation.backtest import build_direct_baselines

from catboost_floader.app.direct_robustness import _safe_float
from catboost_floader.app.pipeline_utils import _drop_non_model_columns


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


def _main_holdout_safeguard_policy() -> Dict[str, Any]:
    return {
        "enabled": bool(MAIN_HOLDOUT_SAFEGUARD_ENABLED),
        "model_key": str(MAIN_HOLDOUT_SAFEGUARD_MODEL_KEY),
        "min_points": int(MAIN_HOLDOUT_SAFEGUARD_MIN_POINTS),
        "max_relative_underperformance": float(MAIN_HOLDOUT_SAFEGUARD_MAX_RELATIVE_UNDERPERFORMANCE),
    }


def _evaluate_holdout_vs_persistence(
    direct_model,
    X_holdout_full: pd.DataFrame,
    y_holdout: pd.DataFrame,
) -> Dict[str, Any]:
    if X_holdout_full.empty or y_holdout.empty:
        return {
            "evaluated": False,
            "row_count": int(len(X_holdout_full)),
            "strategy_mae": None,
            "persistence_mae": None,
            "delta_vs_persistence": None,
            "relative_underperformance_vs_persistence": None,
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
        }

    pred_return = np.asarray(direct_model.predict(X_aligned), dtype=float)
    pred_price = close * (1.0 + pred_return)

    baselines = build_direct_baselines(X_holdout_full)
    persistence_series = baselines.get("baseline_persistence_return")
    if persistence_series is None:
        persistence_return = np.zeros(len(close), dtype=float)
    else:
        persistence_return = pd.to_numeric(persistence_series, errors="coerce").fillna(0.0).to_numpy(dtype=float)
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
        }

    strategy_mae = float(np.mean(np.abs(target_price - pred_price)))
    persistence_mae = float(np.mean(np.abs(target_price - persistence_price)))
    delta = float(persistence_mae - strategy_mae)
    relative_underperformance = float(max(0.0, strategy_mae - persistence_mae) / max(abs(persistence_mae), 1e-8))

    return {
        "evaluated": True,
        "row_count": int(len(target_price)),
        "strategy_mae": strategy_mae,
        "persistence_mae": persistence_mae,
        "delta_vs_persistence": delta,
        "relative_underperformance_vs_persistence": relative_underperformance,
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
    evaluation = _evaluate_holdout_vs_persistence(direct_model, X_holdout_full, y_holdout)

    payload = {
        "policy": policy,
        "evaluated": bool(evaluation.get("evaluated", False)),
        "applied": False,
        "reason": "not_applicable",
        "row_count": int(evaluation.get("row_count", 0) or 0),
        "strategy_mae": _safe_float(evaluation.get("strategy_mae")),
        "persistence_mae": _safe_float(evaluation.get("persistence_mae")),
        "delta_vs_persistence": _safe_float(evaluation.get("delta_vs_persistence")),
        "relative_underperformance_vs_persistence": _safe_float(evaluation.get("relative_underperformance_vs_persistence")),
    }

    strategy_out = dict(direct_strategy or {})
    strategy_out["holdout_delta_vs_persistence"] = payload["delta_vs_persistence"]
    strategy_out["holdout_relative_underperformance_vs_persistence"] = payload["relative_underperformance_vs_persistence"]
    strategy_out["final_holdout_safeguard_applied"] = False

    if not policy["enabled"]:
        payload["reason"] = "disabled_in_config"
        return strategy_out, payload
    if str(model_key) != str(policy["model_key"]):
        payload["reason"] = "non_main_model"
        return strategy_out, payload
    if not payload["evaluated"]:
        payload["reason"] = "holdout_not_evaluable"
        return strategy_out, payload
    if payload["row_count"] < int(policy["min_points"]):
        payload["reason"] = "insufficient_holdout_points"
        return strategy_out, payload

    delta = _safe_float(payload.get("delta_vs_persistence"))
    relative_underperformance = _safe_float(payload.get("relative_underperformance_vs_persistence"))
    if delta is None or relative_underperformance is None:
        payload["reason"] = "missing_holdout_metrics"
        return strategy_out, payload

    if delta < 0.0 and relative_underperformance <= float(policy["max_relative_underperformance"]):
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
            "holdout_delta_vs_persistence": delta,
            "holdout_relative_underperformance_vs_persistence": relative_underperformance,
            "final_holdout_safeguard_applied": True,
        }
        payload["applied"] = True
        payload["reason"] = "marginal_negative_vs_persistence"
        return fallback, payload

    payload["reason"] = "not_triggered"
    return strategy_out, payload
