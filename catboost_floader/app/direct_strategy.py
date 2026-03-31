from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    DIRECT_STRATEGY_ROBUSTNESS_ENABLED,
    DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT,
    DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO,
)
from catboost_floader.evaluation.backtest import build_direct_baselines
from catboost_floader.models.direct import resolve_direct_composition_config

from catboost_floader.app.composition_profiles import (
    _direct_profile_key,
    _direct_profile_sequence,
    _direct_strategy_candidates,
    _direct_strategy_model_weight,
)
from catboost_floader.app.direct_robustness import (
    _compute_direct_candidate_multi_window,
    _extract_robustness_metrics,
    _robustness_comparison_key,
    _direct_strategy_passes_robustness,
)
from catboost_floader.app.direct_strategy_guard import _apply_persistence_guard
from catboost_floader.app.pipeline_utils import _drop_non_model_columns


def _select_direct_strategy(direct_model, X_val_full: pd.DataFrame, y_val: pd.DataFrame) -> Dict[str, object]:
    if X_val_full.empty or y_val.empty:
        return {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}

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

    profile_sequence_keys: list[str] = []
    profile_results: dict[str, Dict[str, Any]] = {}
    for profile_name in _direct_profile_sequence(direct_model):
        strategy_cfg = resolve_direct_composition_config(profile_name)
        profile_key = _direct_profile_key(profile_name)
        profile_sequence_keys.append(profile_key)
        if not bool(strategy_cfg.get("profile_enabled", True)):
            profile_results[profile_key] = {
                "profile": profile_key,
                "config": dict(strategy_cfg),
                "validation_mae": None,
                "status": "config_disabled",
                "selected": None,
                "candidate_count": 0,
                "robust_candidate_count": 0,
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
            mae = float(np.mean(np.abs(target_price - pred_price)))
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
            candidate_evaluations.append(
                {
                    "strategy": dict(strategy),
                    "validation_mae": float(mae),
                    "robustness_metrics": robustness_metrics,
                    "robustness_summary": multi_window_summary,
                    "robustness_gate_pass": bool(robustness_gate_pass),
                    "robustness_gate_reasons": list(robustness_gate_reasons),
                }
            )

        if not candidate_evaluations:
            profile_results[profile_key] = {
                "profile": profile_key,
                "config": dict(strategy_cfg),
                "validation_mae": None,
                "status": "no_candidates",
                "selected": None,
                "candidate_count": 0,
                "robust_candidate_count": 0,
            }
            continue

        robust_candidates = [c for c in candidate_evaluations if c["robustness_gate_pass"]]
        selection_pool = robust_candidates if robust_candidates else candidate_evaluations
        selection_pool_name = "robustness_gate_pass" if robust_candidates else "all_candidates_fallback"

        profile_best = selection_pool[0]
        for candidate in selection_pool[1:]:
            candidate_mae = float(candidate["validation_mae"])
            best_mae = float(profile_best["validation_mae"])
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
                    if prefer_model_tol > 0:
                        mae_tol = max(best_mae, 1e-8) * prefer_model_tol
                        if (
                            abs(candidate_mae - best_mae) <= mae_tol
                            and _direct_strategy_model_weight(candidate["strategy"]) > _direct_strategy_model_weight(profile_best["strategy"])
                        ):
                            profile_best = candidate
                    elif _direct_strategy_model_weight(candidate["strategy"]) < _direct_strategy_model_weight(profile_best["strategy"]):
                        profile_best = candidate

        selected = dict(profile_best["strategy"])
        selected["validation_mae"] = float(profile_best["validation_mae"])
        selected["composition_profile"] = profile_key
        selected["selection_pool"] = selection_pool_name
        selected["robustness_metrics"] = dict(profile_best["robustness_metrics"])
        selected["robustness_gate_pass"] = bool(profile_best["robustness_gate_pass"])
        selected["robustness_gate_reasons"] = list(profile_best["robustness_gate_reasons"])

        profile_results[profile_key] = {
            "profile": profile_key,
            "config": dict(strategy_cfg),
            "validation_mae": float(profile_best["validation_mae"]),
            "status": "candidate",
            "selected": selected,
            "candidate_count": len(candidates),
            "robust_candidate_count": len(robust_candidates),
            "robustness_gate_pass": bool(profile_best["robustness_gate_pass"]),
            "robustness_gate_reasons": list(profile_best["robustness_gate_reasons"]),
            "robustness_metrics": dict(profile_best["robustness_metrics"]),
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
        }
        if result.get("selected") is not None:
            record["strategy"] = dict(result["selected"])

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
                result["status"] = "inactive_default_dominates"
            elif rel_improvement <= min_improvement + 1e-12:
                result["status"] = "fallback_default"
            else:
                result["status"] = "eligible"

        if (
            profile_key != "default"
            and result.get("selected") is not None
            and DIRECT_STRATEGY_ROBUSTNESS_ENABLED
            and DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT
            and not bool(result.get("robustness_gate_pass", False))
        ):
            result["status"] = "fallback_default_not_robust"

        record["status"] = result.get("status", record["status"])
        evaluation_log.append(record)

        if result.get("selected") is None:
            continue
        if profile_key == "default":
            selectable_results.append(result)
        elif result.get("status") == "eligible":
            if not (DIRECT_STRATEGY_ROBUSTNESS_ENABLED and DIRECT_STRATEGY_ROBUSTNESS_REQUIRED_FOR_NON_DEFAULT):
                selectable_results.append(result)
            elif bool(result.get("robustness_gate_pass", False)):
                selectable_results.append(result)

    if not selectable_results:
        return {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}

    best_result = selectable_results[0]
    for candidate in selectable_results[1:]:
        candidate_mae = float(candidate["validation_mae"])
        best_mae = float(best_result["validation_mae"])
        mae_close_tol = max(max(best_mae, candidate_mae), 1e-8) * float(DIRECT_STRATEGY_ROBUSTNESS_MAE_TOLERANCE_RATIO)
        if candidate_mae < best_mae - 1e-12:
            best_result = candidate
            continue
        if abs(candidate_mae - best_mae) <= max(mae_close_tol, 1e-12):
            candidate_rob = _robustness_comparison_key(dict(candidate.get("robustness_metrics", {}) or {}))
            best_rob = _robustness_comparison_key(dict(best_result.get("robustness_metrics", {}) or {}))
            if candidate_rob > best_rob:
                best_result = candidate
                continue
            candidate_weight = _direct_strategy_model_weight(candidate["selected"])
            best_weight = _direct_strategy_model_weight(best_result["selected"])
            if candidate_weight < best_weight:
                best_result = candidate

    best = dict(best_result["selected"])
    best["profile_selection_mode"] = "validation_plus_multi_window_robustness"
    best["profile_evaluations"] = evaluation_log
    best["default_validation_mae"] = default_mae
    best["selected_profile_status"] = best_result.get("status", "eligible")
    best["selected_profile_robustness"] = dict(best_result.get("robustness_metrics", {}) or {})
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
        return safe_strategy

    direct_model.composition_profile = best_profile
    if best_cfg is not None:
        direct_model.composition_config = dict(best_cfg)
    return best
