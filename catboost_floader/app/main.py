from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from catboost_floader.monitoring.anomaly_cleaning import annotate_anomalies, clean_training_anomalies, persist_anomaly_artifacts
from catboost_floader.evaluation.backtest import build_direct_baselines, run_backtest, split_train_test
from catboost_floader.models.confidence import fit_error_calibrator
from catboost_floader.core.config import (
    BACKTEST_DIR,
    DIRECT_CATBOOST_PARAMS,
    ENABLE_PARALLEL_CPU_BACKTEST,
    PARALLEL_BACKTEST_WORKERS,
    PARALLEL_MULTI_MODEL_WORKERS,
    RANGE_HIGH_CATBOOST_PARAMS,
    RANGE_LOW_CATBOOST_PARAMS,
    MODEL_DIR,
    REPORT_DIR,
    TRAIN_LOOKBACK_DAYS,
    MULTI_TIMEFRAMES,
    MULTI_HORIZONS_HOURS,
    DIRECTION_DEADBAND,
    MULTI_PERSIST_AGGREGATED,
    MULTI_AGGREGATED_DIR,
    MULTI_SKIP_TUNING,
    apply_cpu_worker_limits,
    resolve_parallel_cpu_settings,
)
from catboost_floader.data.ingestion import assemble_market_dataset
from catboost_floader.data.preprocessing import preprocess_data
from catboost_floader.features.engineering import build_direct_features, build_range_features
from catboost_floader.models.tuning import tune_direct_model, tune_range_high_model, tune_range_low_model
from catboost_floader.models.direct import resolve_direct_composition_config, train_direct_model
from catboost_floader.models.range import train_range_model
from catboost_floader.targets.generation import generate_direct_targets, generate_range_targets
from catboost_floader.core.utils import ensure_dirs, get_logger, save_json

logger = get_logger("app_main")


def _feature_stats(df: pd.DataFrame) -> dict:
    stats = {}
    for col in df.columns:
        if col == "timestamp":
            continue
        ser = df[col]
        # Prefer numeric reductions; coerce when possible, otherwise set sensible defaults
        if is_numeric_dtype(ser.dtype):
            mean_val = float(ser.mean())
            std_val = float(ser.std() + 1e-8)
        else:
            coerced = pd.to_numeric(ser, errors="coerce")
            if coerced.notna().any():
                mean_val = float(coerced.mean())
                std_val = float(coerced.std() + 1e-8)
            else:
                mean_val = 0.0
                std_val = 1.0
        stats[col] = {"mean": mean_val, "std": std_val}
    return stats


def _save_feature_importance(
    direct_model,
    range_model,
    direct_cols: list[str],
    range_cols: list[str],
    report_dir: str = REPORT_DIR,
) -> None:
    try:
        direct_imp = {}
        try:
            if getattr(direct_model, "movement_model", None) is not None and getattr(direct_model.movement_model, "model", None) is not None:
                direct_imp = dict(zip(direct_cols, map(float, direct_model.movement_model.model.get_feature_importance())))
            elif getattr(direct_model, "model", None) is not None:
                direct_imp = dict(zip(direct_cols, map(float, direct_model.model.get_feature_importance())))
        except Exception:
            direct_imp = {}
    except Exception:
        direct_imp = {}
    try:
        low_imp = dict(zip(range_cols, map(float, range_model.low_model.get_feature_importance()))) if getattr(range_model, "low_model", None) is not None else {}
        high_imp = dict(zip(range_cols, map(float, range_model.high_model.get_feature_importance()))) if getattr(range_model, "high_model", None) is not None else {}
    except Exception:
        low_imp, high_imp = {}, {}
    save_json({"direct": direct_imp, "range_low": low_imp, "range_high": high_imp}, os.path.join(report_dir, "feature_importance.json"))


def _drop_non_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    drop_cols = ["timestamp"]
    drop_cols += [c for c in out.columns if str(c).startswith("target_")]
    out = out.drop(columns=drop_cols, errors="ignore")

    # Keep only numeric columns to avoid passing categorical/text data into CatBoost
    numeric = out.select_dtypes(include=[np.number])
    return numeric.copy()


def _split_train_val(X: pd.DataFrame, y: pd.DataFrame, val_size: float = 0.15) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    split_idx = max(1, int(len(X) * (1 - val_size)))
    return (
        X.iloc[:split_idx].copy().reset_index(drop=True),
        X.iloc[split_idx:].copy().reset_index(drop=True),
        y.iloc[:split_idx].copy().reset_index(drop=True),
        y.iloc[split_idx:].copy().reset_index(drop=True),
    )


def _sync_branch_pair(
    X_direct: pd.DataFrame,
    y_direct: pd.DataFrame,
    X_range: pd.DataFrame,
    y_range: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if X_direct.empty or X_range.empty:
        return (
            X_direct.iloc[0:0].copy().reset_index(drop=True),
            y_direct.iloc[0:0].copy().reset_index(drop=True),
            X_range.iloc[0:0].copy().reset_index(drop=True),
            y_range.iloc[0:0].copy().reset_index(drop=True),
        )

    common_ts = pd.Index(X_direct["timestamp"]).intersection(pd.Index(X_range["timestamp"]))
    if len(common_ts) == 0:
        return (
            X_direct.iloc[0:0].copy().reset_index(drop=True),
            y_direct.iloc[0:0].copy().reset_index(drop=True),
            X_range.iloc[0:0].copy().reset_index(drop=True),
            y_range.iloc[0:0].copy().reset_index(drop=True),
        )

    left_idx = X_direct.loc[X_direct["timestamp"].isin(common_ts)].sort_values("timestamp").index
    right_idx = X_range.loc[X_range["timestamp"].isin(common_ts)].sort_values("timestamp").index

    X_direct2 = X_direct.loc[left_idx].copy().reset_index(drop=True)
    y_direct2 = y_direct.loc[left_idx].copy().reset_index(drop=True)
    X_range2 = X_range.loc[right_idx].copy().reset_index(drop=True)
    y_range2 = y_range.loc[right_idx].copy().reset_index(drop=True)
    return X_direct2, y_direct2, X_range2, y_range2


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


def _prepare_catboost_params(params: Dict[str, Any] | None, default_params: Dict[str, Any], thread_count: int | None) -> Dict[str, Any] | None:
    if params is None and thread_count is None:
        return None
    out = dict(params) if params is not None else dict(default_params)
    out.pop("task_type", None)
    out.pop("devices", None)
    out.pop("gpu_ram_part", None)
    if thread_count is not None:
        out["thread_count"] = max(1, int(thread_count))
    return out


def _multi_model_artifact_paths(key: str) -> Dict[str, str]:
    model_dir = os.path.join(MODEL_DIR, "multi_models", key)
    report_dir = os.path.join(REPORT_DIR, "multi_models", key)
    backtest_dir = os.path.join(BACKTEST_DIR, "multi_models", key)
    return {
        "model_dir": model_dir,
        "report_dir": report_dir,
        "backtest_dir": backtest_dir,
        "direct_model": os.path.join(model_dir, "direct_model.json"),
        "range_model": os.path.join(model_dir, "range_model.json"),
        "feature_stats": os.path.join(model_dir, "feature_stats.json"),
        "feature_importance": os.path.join(report_dir, "feature_importance.json"),
        "backtest_results": os.path.join(backtest_dir, "backtest_results.csv"),
        "raw_predictions": os.path.join(backtest_dir, "raw_predictions.csv"),
        "baseline_outputs": os.path.join(backtest_dir, "baseline_outputs.csv"),
        "comparison_vs_baselines": os.path.join(backtest_dir, "comparison_vs_baselines.json"),
        "direction_outputs": os.path.join(backtest_dir, "direction_outputs.csv"),
        "movement_outputs": os.path.join(backtest_dir, "movement_outputs.csv"),
        "pipeline_metadata": os.path.join(backtest_dir, "pipeline_metadata.json"),
    }


def _initialize_multi_model_worker(thread_count: int) -> None:
    apply_cpu_worker_limits(thread_count, mark_outer_parallel=True)


def _select_direct_strategy(direct_model, X_val_full: pd.DataFrame, y_val: pd.DataFrame) -> Dict[str, object]:
    if X_val_full.empty or y_val.empty:
        return {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}

    X_model = _drop_non_model_columns(X_val_full)
    X_model_aligned = X_model.reindex(columns=direct_model.feature_names, fill_value=0.0)
    close = pd.to_numeric(X_val_full["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    target_price = pd.to_numeric(y_val["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
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
        profile_best = None
        profile_best_mae = np.inf

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
            is_better = mae < profile_best_mae - 1e-12
            if not is_better and profile_best is not None:
                mae_gap = abs(mae - profile_best_mae)
                if prefer_model_tol > 0:
                    mae_tol = max(profile_best_mae, 1e-8) * prefer_model_tol
                    if mae_gap <= mae_tol and _direct_strategy_model_weight(strategy) > _direct_strategy_model_weight(profile_best):
                        is_better = True
                elif mae_gap <= 1e-12 and _direct_strategy_model_weight(strategy) < _direct_strategy_model_weight(profile_best):
                    is_better = True
            if is_better:
                profile_best_mae = mae
                profile_best = dict(strategy)
                profile_best["validation_mae"] = mae
                profile_best["composition_profile"] = profile_key

        profile_results[profile_key] = {
            "profile": profile_key,
            "config": dict(strategy_cfg),
            "validation_mae": None if profile_best is None else float(profile_best_mae),
            "status": "candidate",
            "selected": profile_best,
            "candidate_count": len(candidates),
        }

    # Safety guard: never choose a strategy that is noticeably worse than
    # pure persistence baseline on validation.
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

        record["status"] = result.get("status", record["status"])
        evaluation_log.append(record)

        if result.get("selected") is None:
            continue
        if profile_key == "default" or result.get("status") == "eligible":
            selectable_results.append(result)

    if not selectable_results:
        return {"type": "model_only", "alpha": 1.0, "baseline": "persistence"}

    best_result = selectable_results[0]
    for candidate in selectable_results[1:]:
        candidate_mae = float(candidate["validation_mae"])
        best_mae = float(best_result["validation_mae"])
        if candidate_mae < best_mae - 1e-12:
            best_result = candidate
            continue
        if abs(candidate_mae - best_mae) <= 1e-12:
            candidate_weight = _direct_strategy_model_weight(candidate["selected"])
            best_weight = _direct_strategy_model_weight(best_result["selected"])
            if candidate_weight < best_weight:
                best_result = candidate

    best = dict(best_result["selected"])
    best["profile_selection_mode"] = "validation_driven_fallback"
    best["profile_evaluations"] = evaluation_log
    best["default_validation_mae"] = default_mae
    best["selected_profile_status"] = best_result.get("status", "eligible")
    best_cfg = dict(best_result["config"])
    best_profile = best_result["profile"]
    best_mae = float(best_result["validation_mae"])

    # если стратегия хуже persistence даже с небольшим допуском — принудительно используем persistence
    tolerance = float((best_cfg or {}).get("strategy_persistence_guard_tolerance", 0.005))
    if base_mae > 0 and best_mae > base_mae * (1.0 + tolerance):
        safe_strategy = {
            "type": "baseline_only",
            "alpha": 0.0,
            "baseline": "persistence",
            "validation_mae": base_mae,
            "composition_profile": best_profile,
            "profile_selection_mode": "validation_driven_fallback",
            "profile_evaluations": evaluation_log,
            "default_validation_mae": default_mae,
            "selected_profile_status": best_result.get("status", "eligible"),
        }
        direct_model.composition_profile = best_profile
        if best_cfg is not None:
            direct_model.composition_config = dict(best_cfg)
        direct_model.strategy = safe_strategy
        return safe_strategy

    direct_model.composition_profile = best_profile
    if best_cfg is not None:
        direct_model.composition_config = dict(best_cfg)
    return best


def _calibrate_range_model(range_model, direct_model, X_range_val_full: pd.DataFrame, X_direct_val_full: pd.DataFrame, y_direct_val: pd.DataFrame) -> Dict[str, float]:
    # Sync branches strictly by timestamp before any numeric operations.
    X_direct_val_full, y_direct_val, X_range_val_full, _dummy = _sync_branch_pair(
        X_direct_val_full,
        y_direct_val,
        X_range_val_full,
        pd.DataFrame({"timestamp": X_range_val_full["timestamp"]}) if not X_range_val_full.empty else pd.DataFrame({"timestamp": []}),
    )
    if X_range_val_full.empty or X_direct_val_full.empty or y_direct_val.empty:
        return {"scale_normal": 1.0, "scale_anomaly": 1.2, "margin_normal": 0.0, "margin_anomaly": 0.0, "center_mode": "direct_center"}

    X_range_model = _drop_non_model_columns(X_range_val_full)
    X_direct_model = _drop_non_model_columns(X_direct_val_full)

    low_raw = np.asarray(range_model.low_model.predict(X_range_model.reindex(columns=range_model.feature_names, fill_value=0.0)), dtype=float)
    high_raw = np.asarray(range_model.high_model.predict(X_range_model.reindex(columns=range_model.feature_names, fill_value=0.0)), dtype=float)
    low_raw, high_raw = np.minimum(low_raw, high_raw), np.maximum(low_raw, high_raw)
    model_center = (low_raw + high_raw) / 2.0
    model_half = np.maximum((high_raw - low_raw) / 2.0, 1e-8)

    direct_pred_return = np.asarray(direct_model.predict(X_direct_model), dtype=float)
    current_close = pd.to_numeric(X_direct_val_full["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    direct_center = current_close * (1.0 + direct_pred_return)

    actual = pd.to_numeric(y_direct_val["target_future_close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    anomaly_flags = pd.to_numeric(X_direct_val_full.get("anomaly_flag", 0), errors="coerce").fillna(0).to_numpy(dtype=int)
    normal_mask = anomaly_flags == 0
    anomaly_mask = anomaly_flags == 1

    best = None
    best_width = np.inf
    target_normal_coverage = 0.9
    # allow tighter scaling factors so we can seek narrower bands
    scale_grid = [0.05, 0.1, 0.15, 0.2, 0.25, 0.33, 0.5]

    for center_mode, center in [("direct_center", direct_center), ("model_center", model_center)]:
        base_half = np.maximum(model_half, np.abs(model_center - center))
        for scale in scale_grid:
            scaled_half = base_half * scale
            residual = np.maximum(np.abs(actual - center) - scaled_half, 0.0)
            # Use a high quantile for margins instead of the absolute max to avoid single-outlier blowups
            margin_normal = float(np.quantile(residual[normal_mask], target_normal_coverage)) if normal_mask.any() else 0.0
            margin_anomaly = float(np.quantile(residual[anomaly_mask], 0.98)) if anomaly_mask.any() else margin_normal
            final_half = scaled_half.copy()
            final_half[normal_mask] += margin_normal
            final_half[anomaly_mask] += margin_anomaly
            avg_norm_width = float(np.mean((2.0 * final_half) / (np.abs(actual) + 1e-8)))
            normal_coverage = float(
                np.mean(
                    np.abs(actual[normal_mask] - center[normal_mask]) <= final_half[normal_mask]
                )
            ) if normal_mask.any() else 1.0
            if normal_coverage >= target_normal_coverage and avg_norm_width < best_width:
                best_width = avg_norm_width
                best = {
                    "scale_normal": float(scale),
                    "scale_anomaly": float(max(scale, 1.0)),
                    "margin_normal": float(margin_normal),
                    "margin_anomaly": float(max(margin_anomaly, margin_normal)),
                    "center_mode": center_mode,
                    "validation_normal_coverage": normal_coverage,
                    "validation_avg_norm_width": avg_norm_width,
                }

    if best is None:
        residual = np.maximum(np.abs(actual - direct_center) - model_half, 0.0)
        best = {
            "scale_normal": 1.0,
            "scale_anomaly": 1.2,
            "margin_normal": float(residual[normal_mask].max()) if normal_mask.any() else 0.0,
            "margin_anomaly": float(np.quantile(residual[anomaly_mask], 0.98)) if anomaly_mask.any() else 0.0,
            "center_mode": "direct_center",
        }
    return best


def _prepare_pipeline_splits(
    direct_features: pd.DataFrame,
    range_features: pd.DataFrame,
    direct_targets: pd.DataFrame,
    range_targets: pd.DataFrame,
    *,
    persist_anomalies: bool = False,
) -> Dict[str, Any]:
    direct_merged = direct_features.merge(direct_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    range_merged = range_features.merge(range_targets, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)

    direct_target_cols = [c for c in direct_merged.columns if str(c).startswith("target_")]
    range_target_cols = [c for c in range_merged.columns if str(c).startswith("target_")]

    X_direct = direct_merged.drop(columns=direct_target_cols, errors="ignore")
    y_direct = direct_merged[["target_future_close", "target_return", "target_log_return"]].copy()
    X_range = range_merged.drop(columns=range_target_cols, errors="ignore")
    y_range = range_merged[["target_range_low", "target_range_high"]].copy()

    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    X_direct = annotate_anomalies(X_direct)
    X_range = annotate_anomalies(X_range)
    if persist_anomalies:
        persist_anomaly_artifacts(X_direct)

    X_direct, y_direct = clean_training_anomalies(X_direct, y_direct)
    X_range, y_range = clean_training_anomalies(X_range, y_range)
    X_direct, y_direct, X_range, y_range = _sync_branch_pair(X_direct, y_direct, X_range, y_range)

    (X_direct_train, X_direct_test), (y_direct_train, y_direct_test) = split_train_test(X_direct, y_direct)
    (X_range_train, X_range_test), (y_range_train, y_range_test) = split_train_test(X_range, y_range)

    X_direct_train, y_direct_train, X_range_train, y_range_train = _sync_branch_pair(X_direct_train, y_direct_train, X_range_train, y_range_train)
    X_direct_test, y_direct_test, X_range_test, y_range_test = _sync_branch_pair(X_direct_test, y_direct_test, X_range_test, y_range_test)

    X_direct_fit, X_direct_val, y_direct_fit, y_direct_val = _split_train_val(X_direct_train, y_direct_train, val_size=0.15)
    X_range_fit, X_range_val, y_range_fit, y_range_val = _split_train_val(X_range_train, y_range_train, val_size=0.15)

    X_direct_fit, y_direct_fit, X_range_fit, y_range_fit = _sync_branch_pair(X_direct_fit, y_direct_fit, X_range_fit, y_range_fit)
    X_direct_val, y_direct_val, X_range_val, y_range_val = _sync_branch_pair(X_direct_val, y_direct_val, X_range_val, y_range_val)

    X_direct_fit_model = _drop_non_model_columns(X_direct_fit)
    X_direct_test_model = _drop_non_model_columns(X_direct_test)
    X_range_fit_model = _drop_non_model_columns(X_range_fit)
    X_range_test_model = _drop_non_model_columns(X_range_test)

    return {
        "X_direct": X_direct,
        "y_direct": y_direct,
        "X_range": X_range,
        "y_range": y_range,
        "X_direct_train": X_direct_train,
        "y_direct_train": y_direct_train,
        "X_direct_test": X_direct_test,
        "y_direct_test": y_direct_test,
        "X_range_train": X_range_train,
        "y_range_train": y_range_train,
        "X_range_test": X_range_test,
        "y_range_test": y_range_test,
        "X_direct_fit": X_direct_fit,
        "y_direct_fit": y_direct_fit,
        "X_direct_val": X_direct_val,
        "y_direct_val": y_direct_val,
        "X_range_fit": X_range_fit,
        "y_range_fit": y_range_fit,
        "X_range_val": X_range_val,
        "y_range_val": y_range_val,
        "X_direct_fit_model": X_direct_fit_model,
        "X_direct_test_model": X_direct_test_model,
        "X_range_fit_model": X_range_fit_model,
        "X_range_test_model": X_range_test_model,
        "feature_stats": _feature_stats(X_direct_fit_model),
    }


def _tune_pipeline_models(prepared: Dict[str, Any], *, skip_tuning: bool = False) -> Dict[str, object]:
    if skip_tuning:
        return {"direct": None, "range_low": None, "range_high": None}

    return {
        "direct": tune_direct_model(prepared["X_direct_fit_model"], prepared["y_direct_fit"]["target_return"]),
        "range_low": tune_range_low_model(prepared["X_range_fit_model"], prepared["y_range_fit"]["target_range_low"]),
        "range_high": tune_range_high_model(prepared["X_range_fit_model"], prepared["y_range_fit"]["target_range_high"]),
    }


def _export_raw_model_artifacts(backtest_df: pd.DataFrame, output_dir: str) -> None:
    ensure_dirs([output_dir])

    raw_cols = [
        "timestamp",
        "close",
        "target_future_close",
        "target_return",
        "direct_pred_return",
        "direct_pred_price",
        "range_pred_low",
        "range_pred_high",
        "target_range_low",
        "target_range_high",
        "confidence",
        "pred_abs_error",
        "ood_score",
        "anomaly_flag",
        "anomaly_score",
        "anomaly_type",
    ]
    raw_cols = [c for c in raw_cols if c in backtest_df.columns]
    if raw_cols:
        backtest_df[raw_cols].to_csv(os.path.join(output_dir, "raw_predictions.csv"), index=False)

    baseline_cols = [
        "timestamp",
        "close",
        "target_future_close",
        "baseline_persistence_price",
        "baseline_rolling_price",
        "baseline_range_low",
        "baseline_range_high",
    ]
    baseline_cols = [c for c in baseline_cols if c in backtest_df.columns]
    if baseline_cols:
        backtest_df[baseline_cols].to_csv(os.path.join(output_dir, "baseline_outputs.csv"), index=False)

    direction_cols = [
        "timestamp",
        "target_return",
        "direction_pred_label",
        "direction_pred_expectation",
        "direction_proba_neg",
        "direction_proba_zero",
        "direction_proba_pos",
    ]
    direction_cols = [c for c in direction_cols if c in backtest_df.columns]
    if len(direction_cols) > 2:
        backtest_df[direction_cols].to_csv(os.path.join(output_dir, "direction_outputs.csv"), index=False)

    movement_cols = ["timestamp", "target_return", "movement_pred_magnitude", "direct_pred_return"]
    movement_cols = [c for c in movement_cols if c in backtest_df.columns]
    if len(movement_cols) > 2:
        backtest_df[movement_cols].to_csv(os.path.join(output_dir, "movement_outputs.csv"), index=False)


def _run_pipeline_bundle(
    prepared: Dict[str, Any],
    *,
    direct_params=None,
    range_low_params=None,
    range_high_params=None,
    direct_composition_profile: str | None = None,
    catboost_thread_count: int | None = None,
    model_dir: str = MODEL_DIR,
    report_dir: str = REPORT_DIR,
    backtest_dir: str = BACKTEST_DIR,
) -> Dict[str, Any]:
    ensure_dirs([model_dir, report_dir, backtest_dir])

    feature_stats = prepared["feature_stats"]
    save_json(feature_stats, os.path.join(model_dir, "feature_stats.json"))

    direct_params_prepared = _prepare_catboost_params(direct_params, DIRECT_CATBOOST_PARAMS, catboost_thread_count)
    range_low_params_prepared = _prepare_catboost_params(range_low_params, RANGE_LOW_CATBOOST_PARAMS, catboost_thread_count)
    range_high_params_prepared = _prepare_catboost_params(range_high_params, RANGE_HIGH_CATBOOST_PARAMS, catboost_thread_count)

    direct_model = train_direct_model(
        prepared["X_direct_fit_model"],
        prepared["y_direct_fit"],
        params=direct_params_prepared,
        composition_profile=direct_composition_profile,
        save=False,
    )
    direct_strategy = _select_direct_strategy(direct_model, prepared["X_direct_val"], prepared["y_direct_val"])
    direct_model.strategy = direct_strategy

    range_model = train_range_model(
        prepared["X_range_fit_model"],
        prepared["y_range_fit"],
        low_params=range_low_params_prepared,
        high_params=range_high_params_prepared,
        save=False,
    )
    range_calibration = _calibrate_range_model(
        range_model,
        direct_model,
        prepared["X_range_val"],
        prepared["X_direct_val"],
        prepared["y_direct_val"],
    )
    range_model.calibration = range_calibration

    direct_model.save(os.path.join(model_dir, "direct_model"))
    range_model.save(os.path.join(model_dir, "range_model"))
    _save_feature_importance(
        direct_model,
        range_model,
        list(prepared["X_direct_fit_model"].columns),
        list(prepared["X_range_fit_model"].columns),
        report_dir=report_dir,
    )

    train_pred = pd.Series(direct_model.predict(prepared["X_direct_fit"]))
    calibrator = fit_error_calibrator(
        prepared["X_direct_fit_model"],
        train_pred,
        prepared["y_direct_fit"]["target_return"],
        save_path=os.path.join(model_dir, "error_calibrator.pkl"),
    )

    backtest_df, backtest_summary = run_backtest(
        direct_features=prepared["X_direct_test"].reset_index(drop=True),
        range_features=prepared["X_range_test"].reset_index(drop=True),
        direct_targets=prepared["y_direct_test"].reset_index(drop=True),
        range_targets=prepared["y_range_test"].reset_index(drop=True),
        direct_model=direct_model,
        range_model=range_model,
        error_calibrator=calibrator,
        direct_feature_stats=feature_stats,
        output_dir=backtest_dir,
    )
    accuracy_metrics = dict(backtest_summary.get("accuracy_metrics", {}) or {})
    _export_raw_model_artifacts(backtest_df, backtest_dir)
    save_json(
        {
            "direct_strategy": direct_strategy,
            "direct_profile_selection": direct_strategy.get("profile_evaluations", []),
            "range_calibration": range_calibration,
            "rows": {
                "direct_fit": int(len(prepared["X_direct_fit_model"])),
                "direct_val": int(len(prepared["X_direct_val"])),
                "direct_test": int(len(prepared["X_direct_test_model"])),
                "range_fit": int(len(prepared["X_range_fit_model"])),
                "range_val": int(len(prepared["X_range_val"])),
                "range_test": int(len(prepared["X_range_test_model"])),
            },
            "feature_counts": {
                "direct": int(prepared["X_direct_fit_model"].shape[1]),
                "range": int(prepared["X_range_fit_model"].shape[1]),
            },
            "direct_composition_profile": getattr(direct_model, "composition_profile", direct_composition_profile),
            "direct_composition_config": getattr(direct_model, "composition_config", {}),
            "accuracy_metrics": accuracy_metrics,
            "direction_accuracy_pct": accuracy_metrics.get("direction_accuracy_pct"),
            "sign_accuracy_pct": accuracy_metrics.get("sign_accuracy_pct"),
        },
        os.path.join(backtest_dir, "pipeline_metadata.json"),
    )

    return {
        "feature_stats": feature_stats,
        "direct_model": direct_model,
        "direct_strategy": direct_strategy,
        "range_model": range_model,
        "range_calibration": range_calibration,
        "error_calibrator": calibrator,
        "backtest_df": backtest_df,
        "backtest_summary": backtest_summary,
        "accuracy_metrics": accuracy_metrics,
        "direct_composition_profile": getattr(direct_model, "composition_profile", direct_composition_profile),
        "direct_composition_config": getattr(direct_model, "composition_config", {}),
    }


def _run_multi_model_key_task(task: Dict[str, Any]) -> Dict[str, Any]:
    key = task["key"]
    thread_count = task.get("catboost_thread_count")
    outer_parallel_worker = bool(task.get("outer_parallel_worker", False))
    if thread_count is not None:
        apply_cpu_worker_limits(thread_count, mark_outer_parallel=outer_parallel_worker)

    logger.info(
        "CPU-parallel multi-model worker started for %s with catboost_thread_count=%s",
        key,
        thread_count,
    )

    direct_targets_tf = generate_direct_targets(task["df_tf"], horizon_steps=task["steps"])
    range_targets_tf = generate_range_targets(task["df_tf"], future_window=task["steps"])

    prepared_tf = _prepare_pipeline_splits(
        task["direct_features"],
        task["range_features"],
        direct_targets_tf,
        range_targets_tf,
    )
    if (
        prepared_tf["X_direct_fit_model"].empty
        or prepared_tf["X_direct_test_model"].empty
        or prepared_tf["X_range_fit_model"].empty
        or prepared_tf["X_range_test_model"].empty
    ):
        return {
            "status": "skipped",
            "key": key,
            "message": "empty aligned splits after anomaly-aware preparation",
        }

    tuning_tf = _tune_pipeline_models(prepared_tf, skip_tuning=task["skip_tuning"])
    direct_composition_profile = _direct_composition_profile_for_key(key)
    artifacts = _multi_model_artifact_paths(key)
    result_tf = _run_pipeline_bundle(
        prepared_tf,
        direct_params=tuning_tf["direct"],
        range_low_params=tuning_tf["range_low"],
        range_high_params=tuning_tf["range_high"],
        direct_composition_profile=direct_composition_profile,
        catboost_thread_count=thread_count,
        model_dir=artifacts["model_dir"],
        report_dir=artifacts["report_dir"],
        backtest_dir=artifacts["backtest_dir"],
    )

    summary = {
        "rows": int(len(prepared_tf["X_direct_test_model"])),
        "direct_composition_profile": result_tf["direct_composition_profile"],
        "direct_composition_config": result_tf["direct_composition_config"],
        "direct_strategy": result_tf["direct_strategy"],
        "range_calibration": result_tf["range_calibration"],
        "metrics": result_tf["backtest_summary"],
        "accuracy_metrics": result_tf.get("accuracy_metrics", {}),
        "direction_accuracy_pct": result_tf.get("accuracy_metrics", {}).get("direction_accuracy_pct"),
        "sign_accuracy_pct": result_tf.get("accuracy_metrics", {}).get("sign_accuracy_pct"),
        "artifacts": artifacts,
    }
    logger.info("CPU-parallel multi-model worker finished for %s", key)
    return {"status": "ok", "key": key, "summary": summary}


def main() -> None:
    ensure_dirs([BACKTEST_DIR, MODEL_DIR, REPORT_DIR])
    logger.info("GPU post-screening acceleration disabled; using CPU-parallel evaluation/backtests where configured.")

    print("Loading data...")
    raw = assemble_market_dataset(lookback_days=TRAIN_LOOKBACK_DAYS)

    print("Preprocessing...")
    clean = preprocess_data(raw)

    print("Building branch-specific features...")
    direct_features = build_direct_features(clean)
    range_features = build_range_features(clean)

    print("Generating branch-specific targets...")
    direct_targets = generate_direct_targets(clean)
    range_targets = generate_range_targets(clean)

    # Log distribution of generated direction labels (for debugging)
    try:
        arr = pd.to_numeric(direct_targets["target_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        dead = float(DIRECTION_DEADBAND)
        trues = np.sign(arr)
        trues[np.abs(arr) < dead] = 0.0
        counts = {"-1": int((trues == -1).sum()), "0": int((trues == 0).sum()), "1": int((trues == 1).sum())}
        save_json({"target_counts": counts, "rows": int(len(arr))}, os.path.join(REPORT_DIR, "direction_label_generation.json"))
        print(f"Saved direction label distribution to {os.path.join(REPORT_DIR, 'direction_label_generation.json')}")
    except Exception as exc:
        print("Failed to log direction label distribution:", exc)

    print("Preparing shared direct/range pipeline splits...")
    prepared_main = _prepare_pipeline_splits(
        direct_features,
        range_features,
        direct_targets,
        range_targets,
        persist_anomalies=True,
    )

    print("Hyperparameter tuning...")
    tuned_main = _tune_pipeline_models(prepared_main)

    print("Training models...")
    main_result = _run_pipeline_bundle(
        prepared_main,
        direct_params=tuned_main["direct"],
        range_low_params=tuned_main["range_low"],
        range_high_params=tuned_main["range_high"],
        direct_composition_profile=_main_direct_composition_profile(),
        model_dir=MODEL_DIR,
        report_dir=REPORT_DIR,
        backtest_dir=BACKTEST_DIR,
    )

    # Live inference is intentionally disabled for now:
    # - it is orthogonal to backtest quality
    # - it can fail due to runtime data issues / API drift
    live_result = {"status": "skipped"}

    # Multi-timeframe / multi-horizon models
    # Prepare raw dataset once for aggregation to multiple timeframes
    try:
        from catboost_floader.data.preprocessing import aggregate_for_modeling
    except Exception:
        try:
            from catboost_floader.data.preprocessing import _aggregate_for_modeling as aggregate_for_modeling
        except Exception:
            aggregate_for_modeling = None

    multi_models_summary: dict = {}
    try:
        # Prepare raw copy for safe aggregation
        raw_prep = raw.copy()
        raw_prep["timestamp"] = pd.to_datetime(raw_prep["timestamp"], utc=True, errors="coerce")
        raw_prep = raw_prep.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        for col in [c for c in raw_prep.columns if c != "timestamp"]:
            raw_prep[col] = pd.to_numeric(raw_prep[col], errors="coerce")
        raw_prep = raw_prep.dropna(subset=["timestamp"]).ffill().bfill().dropna().reset_index(drop=True)

        if aggregate_for_modeling is not None:
            multi_tasks: list[Dict[str, Any]] = []
            for tf in MULTI_TIMEFRAMES:
                df_tf = aggregate_for_modeling(raw_prep, tf)
                # Optionally persist aggregated timeframe CSV for faster reproducibility
                if MULTI_PERSIST_AGGREGATED:
                    try:
                        ensure_dirs([MULTI_AGGREGATED_DIR])
                        csv_path = os.path.join(MULTI_AGGREGATED_DIR, f"market_aggregated_{tf}min.csv")
                        df_tf.to_csv(csv_path, index=False)
                    except Exception as _exc:
                        print(f"Failed to persist aggregated {tf}min dataset: {_exc}")
                direct_feats_tf = build_direct_features(df_tf)
                range_feats_tf = build_range_features(df_tf)

                for h in MULTI_HORIZONS_HOURS:
                    steps = int((h * 60) // tf)
                    if steps < 1:
                        continue
                    key = f"{tf}min_{h}h"
                    multi_tasks.append(
                        {
                            "key": key,
                            "tf": tf,
                            "hours": h,
                            "steps": steps,
                            "df_tf": df_tf,
                            "direct_features": direct_feats_tf,
                            "range_features": range_feats_tf,
                            "skip_tuning": MULTI_SKIP_TUNING,
                        }
                    )

            if multi_tasks:
                requested_multi_workers = min(PARALLEL_MULTI_MODEL_WORKERS, PARALLEL_BACKTEST_WORKERS)
                multi_workers, multi_threads = resolve_parallel_cpu_settings(
                    len(multi_tasks),
                    requested_multi_workers if ENABLE_PARALLEL_CPU_BACKTEST else 1,
                )
                multi_parallel = bool(ENABLE_PARALLEL_CPU_BACKTEST and multi_workers > 1 and len(multi_tasks) > 1)
                keys_msg = ", ".join(task["key"] for task in multi_tasks)
                logger.info(
                    "CPU-parallel post-screening multi-model evaluation enabled=%s workers=%s catboost_thread_count=%s keys=%s",
                    multi_parallel,
                    multi_workers,
                    multi_threads,
                    keys_msg,
                )
                print(
                    f"CPU-parallel multi-model evaluation enabled={multi_parallel} "
                    f"workers={multi_workers} catboost_thread_count={multi_threads}"
                )

                multi_completed_in_parallel = False
                if multi_parallel:
                    apply_cpu_worker_limits(multi_threads)
                    try:
                        with ProcessPoolExecutor(
                            max_workers=multi_workers,
                            mp_context=mp.get_context("spawn"),
                            initializer=_initialize_multi_model_worker,
                            initargs=(multi_threads,),
                        ) as executor:
                            future_to_key = {
                                executor.submit(
                                    _run_multi_model_key_task,
                                    {**task, "catboost_thread_count": multi_threads, "outer_parallel_worker": True},
                                ): task["key"]
                                for task in multi_tasks
                            }
                            for future in as_completed(future_to_key):
                                key = future_to_key[future]
                                try:
                                    result = future.result()
                                    status = result.get("status")
                                    if status == "ok":
                                        multi_models_summary[key] = result["summary"]
                                        print(f"Multi-model: completed {key}")
                                    elif status == "skipped":
                                        print(f"Skipping {key}: {result.get('message')}")
                                    else:
                                        print(f"multi-model pipeline failed for {key}: {result.get('message')}")
                                except Exception as exc:
                                    print(f"multi-model pipeline failed for {key}: {exc}")
                        multi_completed_in_parallel = True
                    except Exception as exc:
                        logger.warning(
                            "CPU process pool unavailable for multi-model evaluation: %s. Falling back to sequential CPU path.",
                            exc,
                        )

                if not multi_completed_in_parallel:
                    logger.info("CPU-parallel multi-model evaluation disabled; using CPU sequential path for %s keys", len(multi_tasks))
                    for task in multi_tasks:
                        key = task["key"]
                        print(f"Multi-model: processing {key}")
                        try:
                            result = _run_multi_model_key_task(
                                {**task, "catboost_thread_count": multi_threads, "outer_parallel_worker": False}
                            )
                            status = result.get("status")
                            if status == "ok":
                                multi_models_summary[key] = result["summary"]
                            elif status == "skipped":
                                print(f"Skipping {key}: {result.get('message')}")
                            else:
                                print(f"multi-model pipeline failed for {key}: {result.get('message')}")
                        except Exception as exc:
                            print(f"multi-model pipeline failed for {key}: {exc}")
    except Exception as exc:
        print(f"multi-model pipeline aborted: {exc}")

    if multi_models_summary:
        multi_models_summary = {key: multi_models_summary[key] for key in sorted(multi_models_summary)}


    summary = {
        "direct_fit": len(prepared_main["X_direct_fit_model"]),
        "direct_val": len(prepared_main["X_direct_val"]),
        "direct_test": len(prepared_main["X_direct_test_model"]),
        "range_fit": len(prepared_main["X_range_fit_model"]),
        "range_val": len(prepared_main["X_range_val"]),
        "range_test": len(prepared_main["X_range_test_model"]),
        "features_direct": prepared_main["X_direct_fit_model"].shape[1],
        "features_range": prepared_main["X_range_fit_model"].shape[1],
        "backtest_rows": len(main_result["backtest_df"]),
        "direct_composition_profile": main_result["direct_composition_profile"],
        "direct_composition_config": main_result["direct_composition_config"],
        "direct_strategy": main_result["direct_strategy"],
        "range_calibration": main_result["range_calibration"],
        "backtest_summary": main_result["backtest_summary"],
        "accuracy_metrics": main_result.get("accuracy_metrics", {}),
        "direction_accuracy_pct": main_result.get("accuracy_metrics", {}).get("direction_accuracy_pct"),
        "sign_accuracy_pct": main_result.get("accuracy_metrics", {}).get("sign_accuracy_pct"),
        "live": live_result,
    }
    # attach multi-model metrics if computed
    if multi_models_summary:
        summary["multi_models"] = multi_models_summary
    save_json(summary, os.path.join(REPORT_DIR, "pipeline_summary.json"))
    print("Done")


if __name__ == "__main__":
    main()
