from __future__ import annotations

from typing import Any, Dict

from catboost_floader.core.config import (
    ROBUSTNESS_DISABLE_HIGH_STD_DELTA_THRESHOLD,
    ROBUSTNESS_DISABLE_LOW_WIN_RATE_THRESHOLD,
    ROBUSTNESS_DISABLE_NEGATIVE_SNAPSHOT_THRESHOLD,
    ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_MEAN_DELTA,
    ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_WIN_RATE,
    ROBUSTNESS_DISABLE_POOR_MEAN_DELTA_THRESHOLD,
    ROBUSTNESS_DISABLE_POOR_WIN_RATE_THRESHOLD,
    ROBUSTNESS_REGIME_CLASSIFICATION_ENABLED,
    ROBUSTNESS_REGIME_DISABLE_DEADWEIGHT,
    ROBUSTNESS_REGIME_DISABLE_ENABLED,
    ROBUSTNESS_REGIME_DISABLE_LOW_WIN_RATE_HIGH_STD,
    ROBUSTNESS_REGIME_DISABLE_NEGATIVE_SNAPSHOT_AND_POOR_ROBUSTNESS,
    ROBUSTNESS_REGIME_DISABLE_PERSISTENT_LOSER,
    ROBUSTNESS_REGIME_DOWNGRADE_DEGRADED_SELECTION,
    ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_MEAN_DELTA,
    ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_SNAPSHOT_DELTA,
    ROBUSTNESS_STATUS_DEADWEIGHT_MAX_WIN_RATE,
    ROBUSTNESS_STATUS_DEGRADED_MAX_MEAN_DELTA,
    ROBUSTNESS_STATUS_DEGRADED_MAX_SNAPSHOT_DELTA,
    ROBUSTNESS_STATUS_DEGRADED_MAX_WIN_RATE,
    ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_MEAN_DELTA,
    ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_SNAPSHOT_DELTA,
    ROBUSTNESS_STATUS_ROBUST_MAX_STD_DELTA,
    ROBUSTNESS_STATUS_ROBUST_MIN_MEAN_DELTA,
    ROBUSTNESS_STATUS_ROBUST_MIN_SNAPSHOT_DELTA,
    ROBUSTNESS_STATUS_ROBUST_MIN_WIN_RATE,
    ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MAX_WIN_RATE,
    ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MIN_STD_DELTA,
    ROBUSTNESS_STATUS_SNAPSHOT_WINNER_MIN_SNAPSHOT_DELTA,
)

from catboost_floader.app.direct_robustness import _extract_robustness_metrics, _safe_float
from catboost_floader.app.holdout_safeguard import _extract_snapshot_metrics


def _robustness_regime_policy() -> Dict[str, Any]:
    return {
        "classification_enabled": bool(ROBUSTNESS_REGIME_CLASSIFICATION_ENABLED),
        "disable_enabled": bool(ROBUSTNESS_REGIME_DISABLE_ENABLED),
        "downgrade_degraded_selection": bool(ROBUSTNESS_REGIME_DOWNGRADE_DEGRADED_SELECTION),
        "disable_rules": {
            "deadweight": bool(ROBUSTNESS_REGIME_DISABLE_DEADWEIGHT),
            "persistent_loser": bool(ROBUSTNESS_REGIME_DISABLE_PERSISTENT_LOSER),
            "negative_snapshot_and_poor_robustness": bool(ROBUSTNESS_REGIME_DISABLE_NEGATIVE_SNAPSHOT_AND_POOR_ROBUSTNESS),
            "low_win_rate_high_std": bool(ROBUSTNESS_REGIME_DISABLE_LOW_WIN_RATE_HIGH_STD),
        },
        "status_thresholds": {
            "robust": {
                "min_mean_delta_vs_baseline": float(ROBUSTNESS_STATUS_ROBUST_MIN_MEAN_DELTA),
                "min_win_rate_vs_baseline": float(ROBUSTNESS_STATUS_ROBUST_MIN_WIN_RATE),
                "max_std_delta_vs_baseline": float(ROBUSTNESS_STATUS_ROBUST_MAX_STD_DELTA),
                "min_snapshot_delta_vs_baseline": float(ROBUSTNESS_STATUS_ROBUST_MIN_SNAPSHOT_DELTA),
            },
            "snapshot_unstable": {
                "min_snapshot_delta_vs_baseline": float(ROBUSTNESS_STATUS_SNAPSHOT_WINNER_MIN_SNAPSHOT_DELTA),
                "min_std_delta_vs_baseline": float(ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MIN_STD_DELTA),
                "max_win_rate_vs_baseline": float(ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MAX_WIN_RATE),
            },
            "near_baseline": {
                "max_abs_mean_delta_vs_baseline": float(ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_MEAN_DELTA),
                "max_abs_snapshot_delta_vs_baseline": float(ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_SNAPSHOT_DELTA),
            },
            "deadweight": {
                "max_abs_mean_delta_vs_baseline": float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_MEAN_DELTA),
                "max_abs_snapshot_delta_vs_baseline": float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_SNAPSHOT_DELTA),
                "max_win_rate_vs_baseline": float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_WIN_RATE),
            },
            "degraded": {
                "max_mean_delta_vs_baseline": float(ROBUSTNESS_STATUS_DEGRADED_MAX_MEAN_DELTA),
                "max_snapshot_delta_vs_baseline": float(ROBUSTNESS_STATUS_DEGRADED_MAX_SNAPSHOT_DELTA),
                "max_win_rate_vs_baseline": float(ROBUSTNESS_STATUS_DEGRADED_MAX_WIN_RATE),
            },
        },
        "disable_thresholds": {
            "persistent_loser_max_win_rate": float(ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_WIN_RATE),
            "persistent_loser_max_mean_delta": float(ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_MEAN_DELTA),
            "negative_snapshot_threshold": float(ROBUSTNESS_DISABLE_NEGATIVE_SNAPSHOT_THRESHOLD),
            "poor_mean_delta_threshold": float(ROBUSTNESS_DISABLE_POOR_MEAN_DELTA_THRESHOLD),
            "poor_win_rate_threshold": float(ROBUSTNESS_DISABLE_POOR_WIN_RATE_THRESHOLD),
            "low_win_rate_threshold": float(ROBUSTNESS_DISABLE_LOW_WIN_RATE_THRESHOLD),
            "high_std_delta_threshold": float(ROBUSTNESS_DISABLE_HIGH_STD_DELTA_THRESHOLD),
        },
    }


def _classify_robustness_regime(
    *,
    model_key: str,
    backtest_summary: Dict[str, Any],
    multi_window_summary: Dict[str, Any],
    final_holdout_safeguard: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    policy = _robustness_regime_policy()
    robustness_metrics = _extract_robustness_metrics(multi_window_summary)
    snapshot_metrics = _extract_snapshot_metrics(backtest_summary)

    mean_delta = _safe_float(robustness_metrics.get("mean_delta_vs_baseline"))
    win_rate = _safe_float(robustness_metrics.get("win_rate_vs_baseline"))
    std_delta = _safe_float(robustness_metrics.get("std_delta_vs_baseline"))
    snapshot_delta = _safe_float(snapshot_metrics.get("snapshot_delta_vs_baseline"))

    deadweight_cond = (
        mean_delta is not None
        and snapshot_delta is not None
        and win_rate is not None
        and abs(mean_delta) <= float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_MEAN_DELTA)
        and abs(snapshot_delta) <= float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_ABS_SNAPSHOT_DELTA)
        and win_rate <= float(ROBUSTNESS_STATUS_DEADWEIGHT_MAX_WIN_RATE)
    )
    robust_cond = (
        mean_delta is not None
        and snapshot_delta is not None
        and win_rate is not None
        and mean_delta >= float(ROBUSTNESS_STATUS_ROBUST_MIN_MEAN_DELTA)
        and snapshot_delta >= float(ROBUSTNESS_STATUS_ROBUST_MIN_SNAPSHOT_DELTA)
        and win_rate >= float(ROBUSTNESS_STATUS_ROBUST_MIN_WIN_RATE)
        and (std_delta is None or std_delta <= float(ROBUSTNESS_STATUS_ROBUST_MAX_STD_DELTA))
    )
    snapshot_winner_unstable_cond = (
        snapshot_delta is not None
        and snapshot_delta >= float(ROBUSTNESS_STATUS_SNAPSHOT_WINNER_MIN_SNAPSHOT_DELTA)
        and (
            (std_delta is not None and std_delta >= float(ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MIN_STD_DELTA))
            or (
                win_rate is not None
                and mean_delta is not None
                and mean_delta > 0.0
                and win_rate <= float(ROBUSTNESS_STATUS_SNAPSHOT_UNSTABLE_MAX_WIN_RATE)
            )
        )
    )
    near_baseline_cond = (
        mean_delta is not None
        and snapshot_delta is not None
        and abs(mean_delta) <= float(ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_MEAN_DELTA)
        and abs(snapshot_delta) <= float(ROBUSTNESS_STATUS_NEAR_BASELINE_MAX_ABS_SNAPSHOT_DELTA)
    )
    degraded_cond = (
        (mean_delta is not None and mean_delta <= float(ROBUSTNESS_STATUS_DEGRADED_MAX_MEAN_DELTA))
        or (snapshot_delta is not None and snapshot_delta <= float(ROBUSTNESS_STATUS_DEGRADED_MAX_SNAPSHOT_DELTA))
        or (
            mean_delta is not None
            and win_rate is not None
            and mean_delta < 0.0
            and win_rate <= float(ROBUSTNESS_STATUS_DEGRADED_MAX_WIN_RATE)
        )
    )

    if deadweight_cond:
        base_status = "deadweight"
    elif robust_cond:
        base_status = "robust_winner"
    elif snapshot_winner_unstable_cond:
        base_status = "snapshot_winner_unstable"
    elif near_baseline_cond:
        base_status = "near_baseline"
    elif degraded_cond:
        base_status = "degraded"
    else:
        base_status = "near_baseline"

    disable_reasons: list[str] = []
    if bool(ROBUSTNESS_REGIME_DISABLE_ENABLED):
        if deadweight_cond and bool(ROBUSTNESS_REGIME_DISABLE_DEADWEIGHT):
            disable_reasons.append("deadweight_zero_edge")

        persistent_loser_cond = (
            mean_delta is not None
            and win_rate is not None
            and mean_delta <= float(ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_MEAN_DELTA)
            and win_rate <= float(ROBUSTNESS_DISABLE_PERSISTENT_LOSER_MAX_WIN_RATE)
        )
        if persistent_loser_cond and bool(ROBUSTNESS_REGIME_DISABLE_PERSISTENT_LOSER):
            disable_reasons.append("persistent_loser")

        negative_snapshot_poor_cond = (
            snapshot_delta is not None
            and mean_delta is not None
            and win_rate is not None
            and snapshot_delta <= float(ROBUSTNESS_DISABLE_NEGATIVE_SNAPSHOT_THRESHOLD)
            and mean_delta <= float(ROBUSTNESS_DISABLE_POOR_MEAN_DELTA_THRESHOLD)
            and win_rate <= float(ROBUSTNESS_DISABLE_POOR_WIN_RATE_THRESHOLD)
        )
        if negative_snapshot_poor_cond and bool(ROBUSTNESS_REGIME_DISABLE_NEGATIVE_SNAPSHOT_AND_POOR_ROBUSTNESS):
            disable_reasons.append("negative_snapshot_and_poor_robustness")

        low_win_high_std_cond = (
            win_rate is not None
            and std_delta is not None
            and win_rate <= float(ROBUSTNESS_DISABLE_LOW_WIN_RATE_THRESHOLD)
            and std_delta >= float(ROBUSTNESS_DISABLE_HIGH_STD_DELTA_THRESHOLD)
        )
        if low_win_high_std_cond and bool(ROBUSTNESS_REGIME_DISABLE_LOW_WIN_RATE_HIGH_STD):
            disable_reasons.append("low_win_rate_high_std")

    disabled = len(disable_reasons) > 0
    downgraded = False
    selection_eligibility = not disabled
    if not disabled and base_status == "degraded" and bool(ROBUSTNESS_REGIME_DOWNGRADE_DEGRADED_SELECTION):
        selection_eligibility = False
        downgraded = True

    final_status = "disabled" if disabled else base_status
    disable_reason = disable_reasons[0] if disable_reasons else None
    holdout_info = dict(final_holdout_safeguard or {})

    return {
        "model_key": str(model_key),
        "policy": policy,
        "robustness_status": final_status,
        "robustness_base_status": base_status,
        "disabled_by_robustness": bool(disabled),
        "robustness_disable_reason": disable_reason,
        "robustness_disable_reasons": disable_reasons,
        "selection_eligibility": bool(selection_eligibility),
        "downgraded_by_robustness": bool(downgraded),
        "robustness_metrics": robustness_metrics,
        "snapshot_metrics": snapshot_metrics,
        "final_holdout_safeguard_applied": bool(holdout_info.get("applied", False)),
        "final_holdout_safeguard": holdout_info,
    }
