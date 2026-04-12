from __future__ import annotations

from typing import Any, Dict


def _direct_strategy_config(direct_model) -> Dict[str, Any]:
    return dict(getattr(direct_model, "composition_config", {}) or {})


def _direct_strategy_model_weight(strategy: Dict[str, object]) -> float:
    strategy_type = str(strategy.get("type", "model_only"))
    if strategy_type == "model_only":
        return 1.0
    if strategy_type == "blend":
        return float(strategy.get("alpha", 0.0))
    return 0.0


def _direct_composition_profile_for_key(key: str | None) -> str | None:
    if key in {"60min_3h", "60min_6h", "60min_12h"}:
        return key
    return None


def _main_direct_composition_profile() -> str:
    return "main_direct_pipeline"


def _direct_profile_sequence(direct_model) -> list[str | None]:
    active_profile = getattr(direct_model, "composition_profile", None)
    active_cfg = _direct_strategy_config(direct_model)
    profile_sequence: list[str | None] = [active_profile]
    for fallback in active_cfg.get("profile_fallbacks", []):
        fallback_name = str(fallback).strip()
        if fallback_name:
            profile_sequence.append(fallback_name)
    if None not in profile_sequence:
        profile_sequence.append(None)

    unique_profiles: list[str | None] = []
    seen: set[str] = set()
    for profile_name in profile_sequence:
        key = "default" if profile_name in (None, "", "default") else str(profile_name)
        if key in seen:
            continue
        seen.add(key)
        unique_profiles.append(None if key == "default" else key)
    return unique_profiles


def _direct_profile_key(profile_name: str | None) -> str:
    return "default" if profile_name in (None, "", "default") else str(profile_name)


def _direct_strategy_alpha_grid(strategy_cfg: Dict[str, Any]) -> list[float]:
    alpha_grid = []
    for alpha in strategy_cfg.get("strategy_alpha_grid", [0.25, 0.4, 0.55, 0.7, 0.85]):
        try:
            alpha_val = float(alpha)
        except Exception:
            continue
        if 0.0 < alpha_val < 1.0:
            alpha_grid.append(alpha_val)
    return sorted(set(alpha_grid)) or [0.25, 0.4, 0.55, 0.7, 0.85]


def _direct_strategy_candidates(strategy_cfg: Dict[str, Any]) -> list[Dict[str, object]]:
    allow_baseline_only = bool(strategy_cfg.get("strategy_allow_baseline_only", True))
    baselines = []
    for baseline in strategy_cfg.get("strategy_baselines", ["persistence", "rolling_mean"]):
        baseline_name = str(baseline)
        if baseline_name:
            baselines.append(baseline_name)
    baselines = baselines or ["persistence", "rolling_mean"]
    alpha_grid = _direct_strategy_alpha_grid(strategy_cfg)

    candidates: list[Dict[str, object]] = []
    for baseline_name in baselines:
        if allow_baseline_only:
            candidates.append({"type": "baseline_only", "alpha": 0.0, "baseline": baseline_name})
        for alpha in alpha_grid:
            candidates.append({"type": "blend", "alpha": alpha, "baseline": baseline_name})
    candidates.append({"type": "model_only", "alpha": 1.0, "baseline": "persistence"})
    return candidates
