from catboost_floader.selection import holdout_safeguard as hs


def _strategy_stub() -> dict:
    return {
        "type": "blend",
        "alpha": 0.25,
        "baseline": "persistence",
        "validation_mae": 100.0,
        "composition_profile": "main_direct_pipeline",
        "selection_pool": "robustness_gate_pass",
        "profile_selection_mode": "validation_plus_multi_window_robustness",
        "profile_evaluations": [],
        "default_validation_mae": 100.0,
        "selected_profile_status": "eligible",
        "selected_profile_robustness": {},
        "robustness_metrics": {},
        "robustness_gate_reasons": [],
    }


def _patched_eval(delta: float, rel_under: float, rel_impr: float):
    return {
        "evaluated": True,
        "row_count": 2000,
        "strategy_mae": 100.2,
        "persistence_mae": 100.0,
        "delta_vs_persistence": delta,
        "relative_underperformance_vs_persistence": rel_under,
        "relative_improvement_vs_persistence": rel_impr,
        "candidate_descriptor": {
            "type": "blend",
            "alpha": 0.25,
            "baseline": "persistence",
            "validation_mae": 100.0,
            "composition_profile": "main_direct_pipeline",
            "selection_pool": "robustness_gate_pass",
            "profile_selection_mode": "validation_plus_multi_window_robustness",
        },
    }


def test_main_holdout_safeguard_allows_small_negative_delta(monkeypatch):
    monkeypatch.setattr(hs, "_evaluate_holdout_vs_persistence", lambda *args, **kwargs: _patched_eval(-0.2, 0.002, 0.0))

    strategy_out, payload = hs._apply_main_holdout_safeguard(
        model_key="main_direct_pipeline",
        direct_model=None,
        direct_strategy=_strategy_stub(),
        X_holdout_full=None,
        y_holdout=None,
    )

    assert payload["final_holdout_guard_applied"] is False
    assert payload["reason"] == "not_triggered"
    assert strategy_out["type"] == "blend"


def test_main_holdout_safeguard_fallbacks_on_clear_underperformance(monkeypatch):
    monkeypatch.setattr(hs, "_evaluate_holdout_vs_persistence", lambda *args, **kwargs: _patched_eval(-0.6, 0.006, 0.0))

    strategy_out, payload = hs._apply_main_holdout_safeguard(
        model_key="main_direct_pipeline",
        direct_model=None,
        direct_strategy=_strategy_stub(),
        X_holdout_full=None,
        y_holdout=None,
    )

    assert payload["final_holdout_guard_applied"] is True
    assert payload["reason"] == "clear_underperformance_vs_persistence"
    assert strategy_out["type"] == "baseline_only"
