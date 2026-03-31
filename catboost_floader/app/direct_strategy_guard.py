from __future__ import annotations

from typing import Any, Dict

from catboost_floader.app.direct_robustness import (
    _compute_direct_candidate_multi_window,
    _extract_robustness_metrics,
)


def _apply_persistence_guard(
    *,
    base_mae: float,
    best_mae: float,
    best_cfg: Dict[str, Any],
    best_profile: str,
    best_result: Dict[str, Any],
    close,
    target_price,
    target_return,
    baseline_map,
    evaluation_log,
    default_mae,
    direct_model,
):
    tolerance = float((best_cfg or {}).get("strategy_persistence_guard_tolerance", 0.005))
    if not (base_mae > 0 and best_mae > base_mae * (1.0 + tolerance)):
        return None

    safe_multi_window = _compute_direct_candidate_multi_window(
        model_key=f"direct_selection:{best_profile}:persistence_guard",
        close=close,
        target_price=target_price,
        target_return=target_return,
        pred_return=baseline_map["persistence"],
        baseline_persistence_return=baseline_map["persistence"],
    )
    safe_robustness = _extract_robustness_metrics(safe_multi_window)
    safe_strategy = {
        "type": "baseline_only",
        "alpha": 0.0,
        "baseline": "persistence",
        "validation_mae": base_mae,
        "composition_profile": best_profile,
        "profile_selection_mode": "validation_plus_multi_window_robustness",
        "profile_evaluations": evaluation_log,
        "default_validation_mae": default_mae,
        "selected_profile_status": best_result.get("status", "eligible"),
        "selected_profile_robustness": dict(best_result.get("robustness_metrics", {}) or {}),
        "robustness_metrics": safe_robustness,
        "robustness_gate_pass": True,
        "robustness_gate_reasons": ["persistence_guard_fallback"],
    }
    direct_model.composition_profile = best_profile
    if best_cfg is not None:
        direct_model.composition_config = dict(best_cfg)
    direct_model.strategy = safe_strategy
    return safe_strategy
