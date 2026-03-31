from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    DIRECTION_DEADBAND,
    DIRECT_STRATEGY_ROBUSTNESS_ENABLED,
    DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT,
    DIRECT_STRATEGY_ROBUSTNESS_WINDOW_COUNT,
    DIRECT_STRATEGY_ROBUSTNESS_WINDOW_SIZE,
    DIRECT_STRATEGY_ROBUSTNESS_WINDOW_STEP,
    DIRECT_STRATEGY_ROBUSTNESS_MIN_MEAN_DELTA_VS_BASELINE,
    DIRECT_STRATEGY_ROBUSTNESS_MIN_WIN_RATE_VS_BASELINE,
    DIRECT_STRATEGY_ROBUSTNESS_MAX_STD_DELTA_VS_BASELINE,
    DIRECT_STRATEGY_ROBUSTNESS_MIN_SIGN_ACCURACY_PCT,
    DIRECT_STRATEGY_ROBUSTNESS_MAX_LOSING_WINDOWS,
    DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO,
)
from catboost_floader.evaluation.multi_window import evaluate_model_multi_window_in_memory


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        value_f = float(value)
    except Exception:
        return default
    if np.isnan(value_f):
        return default
    return value_f


def _extract_robustness_metrics(multi_window_summary: Dict[str, Any] | None) -> Dict[str, Any]:
    aggregate = dict((multi_window_summary or {}).get("aggregate_metrics", {}) or {})
    window_count = int(aggregate.get("window_count", aggregate.get("windows_evaluated", 0)) or 0)
    win_rate = _safe_float(aggregate.get("win_rate_vs_baseline", aggregate.get("model_win_rate_vs_baseline")))
    return {
        "window_count": window_count,
        "windows_evaluated": window_count,
        "mean_MAE": _safe_float(aggregate.get("mean_MAE")),
        "std_MAE": _safe_float(aggregate.get("std_MAE")),
        "mean_delta_vs_baseline": _safe_float(aggregate.get("mean_delta_vs_baseline")),
        "std_delta_vs_baseline": _safe_float(aggregate.get("std_delta_vs_baseline")),
        "win_rate_vs_baseline": win_rate,
        "model_win_rate_vs_baseline": win_rate,
        "mean_sign_accuracy_pct": _safe_float(aggregate.get("mean_sign_accuracy_pct")),
        "std_sign_accuracy_pct": _safe_float(aggregate.get("std_sign_accuracy_pct")),
        "mean_direction_accuracy_pct": _safe_float(aggregate.get("mean_direction_accuracy_pct")),
        "best_window_delta_vs_baseline": _safe_float(aggregate.get("best_window_delta_vs_baseline")),
        "worst_window_delta_vs_baseline": _safe_float(aggregate.get("worst_window_delta_vs_baseline")),
    }


def _compute_direct_candidate_multi_window(
    *,
    model_key: str,
    close: np.ndarray,
    target_price: np.ndarray,
    target_return: np.ndarray,
    pred_return: np.ndarray,
    baseline_persistence_return: np.ndarray,
) -> Dict[str, Any]:
    pred_price = close * (1.0 + pred_return)
    baseline_persistence_price = close * (1.0 + baseline_persistence_return)
    pred_lbl = np.zeros(len(pred_return), dtype=int)
    pred_lbl[pred_return > float(DIRECTION_DEADBAND)] = 1
    pred_lbl[pred_return < -float(DIRECTION_DEADBAND)] = -1

    candidate_eval_df = pd.DataFrame(
        {
            "target_future_close": target_price,
            "target_return": target_return,
            "direct_pred_return": pred_return,
            "direct_pred_price": pred_price,
            "baseline_persistence_price": baseline_persistence_price,
            "direction_pred_label": pred_lbl,
        }
    )

    multi_window_summary = evaluate_model_multi_window_in_memory(
        candidate_eval_df,
        model_key=model_key,
        window_count=DIRECT_STRATEGY_ROBUSTNESS_WINDOW_COUNT,
        window_size=DIRECT_STRATEGY_ROBUSTNESS_WINDOW_SIZE,
        window_step=DIRECT_STRATEGY_ROBUSTNESS_WINDOW_STEP,
        deadband=DIRECTION_DEADBAND,
    )
    robustness_metrics = _extract_robustness_metrics(multi_window_summary)
    aggregate_metrics = dict(multi_window_summary.get("aggregate_metrics", {}) or {})
    aggregate_metrics.update(robustness_metrics)
    multi_window_summary["aggregate_metrics"] = aggregate_metrics
    return multi_window_summary


def _direct_strategy_passes_robustness(robustness_metrics: Dict[str, Any]) -> tuple[bool, list[str]]:
    if not DIRECT_STRATEGY_ROBUSTNESS_ENABLED:
        return True, []

    reasons: list[str] = []
    mean_delta = _safe_float(robustness_metrics.get("mean_delta_vs_baseline"))
    win_rate = _safe_float(robustness_metrics.get("win_rate_vs_baseline"))
    std_delta = _safe_float(robustness_metrics.get("std_delta_vs_baseline"))
    sign_accuracy_pct = _safe_float(robustness_metrics.get("mean_sign_accuracy_pct"))
    window_count = int(robustness_metrics.get("window_count") or 0)

    if mean_delta is None or mean_delta < float(DIRECT_STRATEGY_ROBUSTNESS_MIN_MEAN_DELTA_VS_BASELINE):
        reasons.append("mean_delta_vs_baseline_below_threshold")
    if win_rate is None or win_rate < float(DIRECT_STRATEGY_ROBUSTNESS_MIN_WIN_RATE_VS_BASELINE):
        reasons.append("win_rate_vs_baseline_below_threshold")

    max_std = float(DIRECT_STRATEGY_ROBUSTNESS_MAX_STD_DELTA_VS_BASELINE)
    if max_std >= 0.0 and std_delta is not None and std_delta > max_std:
        reasons.append("std_delta_vs_baseline_above_threshold")

    if sign_accuracy_pct is None or sign_accuracy_pct < float(DIRECT_STRATEGY_ROBUSTNESS_MIN_SIGN_ACCURACY_PCT):
        reasons.append("mean_sign_accuracy_pct_below_threshold")

    max_losing_windows = int(DIRECT_STRATEGY_ROBUSTNESS_MAX_LOSING_WINDOWS)
    if max_losing_windows >= 0 and window_count > 0 and win_rate is not None:
        winning_windows = int(round(win_rate * window_count))
        losing_windows = max(0, window_count - winning_windows)
        if losing_windows > max_losing_windows:
            reasons.append("too_many_losing_windows")

    return len(reasons) == 0, reasons


def _robustness_comparison_key(robustness_metrics: Dict[str, Any]) -> tuple[float, float, float, float]:
    mean_delta = _safe_float(robustness_metrics.get("mean_delta_vs_baseline"), default=float("-inf"))
    win_rate = _safe_float(robustness_metrics.get("win_rate_vs_baseline"), default=float("-inf"))
    std_delta = _safe_float(robustness_metrics.get("std_delta_vs_baseline"), default=float("inf"))
    sign_accuracy_pct = _safe_float(robustness_metrics.get("mean_sign_accuracy_pct"), default=float("-inf"))
    return (mean_delta, win_rate, -std_delta, sign_accuracy_pct)


def _direct_strategy_robustness_policy() -> Dict[str, Any]:
    return {
        "enabled": bool(DIRECT_STRATEGY_ROBUSTNESS_ENABLED),
        "required_for_non_default": bool(DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT),
        "window_count": int(DIRECT_STRATEGY_ROBUSTNESS_WINDOW_COUNT),
        "window_size": int(DIRECT_STRATEGY_ROBUSTNESS_WINDOW_SIZE),
        "window_step": int(DIRECT_STRATEGY_ROBUSTNESS_WINDOW_STEP),
        "mae_tolerance_ratio": float(DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO),
        "thresholds": {
            "min_mean_delta_vs_baseline": float(DIRECT_STRATEGY_ROBUSTNESS_MIN_MEAN_DELTA_VS_BASELINE),
            "min_win_rate_vs_baseline": float(DIRECT_STRATEGY_ROBUSTNESS_MIN_WIN_RATE_VS_BASELINE),
            "max_std_delta_vs_baseline": float(DIRECT_STRATEGY_ROBUSTNESS_MAX_STD_DELTA_VS_BASELINE),
            "min_sign_accuracy_pct": float(DIRECT_STRATEGY_ROBUSTNESS_MIN_SIGN_ACCURACY_PCT),
            "max_losing_windows": int(DIRECT_STRATEGY_ROBUSTNESS_MAX_LOSING_WINDOWS),
        },
    }


def _direct_strategy_robustness_payload(strategy: Dict[str, Any] | None) -> Dict[str, Any]:
    strategy = dict(strategy or {})
    gate_pass_raw = strategy.get("robustness_gate_pass")
    if gate_pass_raw is None:
        gate_pass = None
    else:
        gate_pass = bool(gate_pass_raw)

    return {
        "policy": _direct_strategy_robustness_policy(),
        "selected_profile_status": strategy.get("selected_profile_status"),
        "selected_profile_robustness": dict(strategy.get("selected_profile_robustness", {}) or {}),
        "selected_strategy_robustness": dict(strategy.get("robustness_metrics", {}) or {}),
        "selected_strategy_gate_pass": gate_pass,
        "selected_strategy_gate_reasons": list(strategy.get("robustness_gate_reasons", []) or []),
        "final_holdout_safeguard_applied": bool(strategy.get("final_holdout_safeguard_applied", False)),
        "holdout_delta_vs_persistence": _safe_float(strategy.get("holdout_delta_vs_persistence")),
        "holdout_relative_underperformance_vs_persistence": _safe_float(strategy.get("holdout_relative_underperformance_vs_persistence")),
        "profile_evaluations": list(strategy.get("profile_evaluations", []) or []),
    }
