from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _score_outputs_dir(path: Path) -> int:
    if not path.exists():
        return -1
    score = 1
    markers = {
        path / "reports" / "pipeline_summary.json": 12,
        path / "backtest_results" / "backtest_summary.json": 10,
        path / "backtest_results" / "pipeline_metadata.json": 8,
        path / "backtest_results" / "multi_models": 5,
        path / "reports": 2,
        path / "artifacts": 1,
        path / "logs": 1,
    }
    for marker, weight in markers.items():
        if marker.exists():
            score += weight
    return score


def _resolve_outputs_dir(project_root: Path = PROJECT_ROOT) -> Path:
    candidates = [
        project_root / "catboost_floader" / "outputs",
        project_root / "outputs",
    ]
    scored = sorted(((candidate, _score_outputs_dir(candidate)) for candidate in candidates), key=lambda item: item[1], reverse=True)
    best_path, best_score = scored[0]
    if best_score >= 0:
        return best_path
    return candidates[0]


OUTPUTS_DIR = _resolve_outputs_dir()
ARTIFACTS_DIR = OUTPUTS_DIR / "artifacts"
LOG_DIR = OUTPUTS_DIR / "logs"
BACKTEST_DIR = OUTPUTS_DIR / "backtest_results"
REPORT_DIR = OUTPUTS_DIR / "reports"


def get_frontend_paths() -> dict[str, str]:
    return {
        "project_root": str(PROJECT_ROOT),
        "outputs_dir": str(OUTPUTS_DIR),
        "artifacts_dir": str(ARTIFACTS_DIR),
        "log_dir": str(LOG_DIR),
        "backtest_dir": str(BACKTEST_DIR),
        "report_dir": str(REPORT_DIR),
    }


def _safe_read_csv(path: Path, parse_ts: bool = True) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path)
    if parse_ts:
        for col in ["timestamp", "start_ts", "end_ts"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


def _safe_read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe_float(value: Any) -> float | None:
    try:
        value_f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(value_f):
        return None
    return value_f


def _safe_int(value: Any) -> int | None:
    try:
        value_i = int(value)
    except (TypeError, ValueError):
        return None
    return value_i


def _accuracy_pct(value: Any) -> float | None:
    value_f = _safe_float(value)
    if value_f is None:
        return None
    return round(value_f * 100.0, 2)


def _lookup_first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _lookup_dict(key: str, *sources: Any) -> dict[str, Any]:
    for source in sources:
        if isinstance(source, dict):
            value = source.get(key)
            if isinstance(value, dict):
                return dict(value)
    return {}


def _lookup_value(key: str, *sources: Any) -> Any:
    for source in sources:
        if isinstance(source, dict) and source.get(key) is not None:
            return source.get(key)
    return None


def _display_model_name(model_key: str) -> str:
    if model_key == "main_direct_pipeline":
        return "Main Pipeline"
    return str(model_key)


def _is_robust_status(status: Any) -> bool:
    status_norm = str(status or "").lower()
    return status_norm.startswith("robust")


def _derive_recommendation_bucket(row: dict[str, Any]) -> str:
    explicit = row.get("recommendation_bucket")
    if explicit:
        return str(explicit)

    eligible = bool(row.get("selection_eligibility", False))
    robust = _is_robust_status(row.get("robustness_status"))
    overfit_status = str(row.get("overfit_status") or "none").lower()
    guarded_delta = _safe_float(row.get("delta_vs_baseline"))
    raw_delta = _safe_float(row.get("raw_model_delta_vs_baseline"))

    if eligible and robust and guarded_delta is not None and guarded_delta > 0:
        return "Preferred"
    if raw_delta is not None and raw_delta > 0 and (guarded_delta is None or guarded_delta <= 0):
        return "Suppressed Edge"
    if eligible and guarded_delta is not None and guarded_delta > 0:
        return "Usable"
    if overfit_status in {"moderate", "severe"}:
        return "Overfit Risk"
    if not eligible:
        return "Suppressed"
    return "Watch"


def _model_artifact_paths(outputs_dir: Path, model_key: str) -> dict[str, Path]:
    is_main = model_key == "main_direct_pipeline"
    backtest_dir = outputs_dir / "backtest_results"
    report_dir = outputs_dir / "reports"
    if not is_main:
        backtest_dir = backtest_dir / "multi_models" / model_key
        report_dir = report_dir / "multi_models" / model_key
    return {
        "backtest_dir": backtest_dir,
        "report_dir": report_dir,
        "feature_importance": report_dir / "feature_importance.json",
        "backtest_summary": backtest_dir / "backtest_summary.json",
        "pipeline_metadata": backtest_dir / "pipeline_metadata.json",
        "multi_window_summary": backtest_dir / "multi_window_summary.json",
        "comparison_vs_baselines": backtest_dir / "comparison_vs_baselines.json",
        "backtest_results": backtest_dir / "backtest_results.csv",
        "direct_backtest_results": backtest_dir / "direct_backtest_results.csv",
        "range_backtest_results": backtest_dir / "range_backtest_results.csv",
        "raw_predictions": backtest_dir / "raw_predictions.csv",
    }


def _extract_guarded_delta(metric_summary: dict[str, Any]) -> float | None:
    direct_model = dict(metric_summary.get("direct_model", {}) or {})
    baselines = dict(metric_summary.get("direct_baselines", {}) or {})
    persistence = dict(baselines.get("persistence", {}) or {})
    direct_mae = _safe_float(direct_model.get("MAE"))
    baseline_mae = _safe_float(persistence.get("MAE"))
    if direct_mae is None or baseline_mae is None:
        return _safe_float(metric_summary.get("final_holdout_delta_vs_baseline"))
    return float(baseline_mae - direct_mae)


def _extract_robustness_metrics(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
    multi_window_summary: dict[str, Any],
) -> dict[str, Any]:
    direct_strategy = _lookup_dict("direct_strategy", metric_summary, pipeline_metadata, summary_seed)
    multi_window_seed = _lookup_dict("multi_window", summary_seed, pipeline_metadata)
    aggregate_metrics = _lookup_dict(
        "aggregate_metrics",
        multi_window_summary,
        multi_window_seed,
    )
    robustness = dict(direct_strategy.get("robustness_metrics", {}) or {})
    if not robustness:
        robustness = aggregate_metrics
    return {
        "robustness_status": _lookup_value("robustness_status", summary_seed, metric_summary, pipeline_metadata),
        "selection_eligibility": bool(
            _lookup_first(
                _lookup_value("selection_eligibility", summary_seed, metric_summary, pipeline_metadata),
                True,
            )
        ),
        "disabled_by_robustness": bool(
            _lookup_first(
                _lookup_value("disabled_by_robustness", summary_seed, metric_summary, pipeline_metadata),
                False,
            )
        ),
        "robustness_disable_reason": _lookup_value(
            "robustness_disable_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "mean_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("mean_delta_vs_baseline"),
                aggregate_metrics.get("mean_delta_vs_baseline"),
            )
        ),
        "std_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("std_delta_vs_baseline"),
                aggregate_metrics.get("std_delta_vs_baseline"),
            )
        ),
        "win_rate_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("win_rate_vs_baseline"),
                robustness.get("model_win_rate_vs_baseline"),
                aggregate_metrics.get("win_rate_vs_baseline"),
                aggregate_metrics.get("model_win_rate_vs_baseline"),
            )
        ),
        "mean_sign_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("mean_sign_accuracy_pct"),
                aggregate_metrics.get("mean_sign_accuracy_pct"),
            )
        ),
        "std_sign_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("std_sign_accuracy_pct"),
                aggregate_metrics.get("std_sign_accuracy_pct"),
            )
        ),
        "mean_direction_accuracy_pct": _safe_float(
            _lookup_first(
                robustness.get("mean_direction_accuracy_pct"),
                aggregate_metrics.get("mean_direction_accuracy_pct"),
            )
        ),
        "best_window_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("best_window_delta_vs_baseline"),
                aggregate_metrics.get("best_window_delta_vs_baseline"),
            )
        ),
        "worst_window_delta_vs_baseline": _safe_float(
            _lookup_first(
                robustness.get("worst_window_delta_vs_baseline"),
                aggregate_metrics.get("worst_window_delta_vs_baseline"),
            )
        ),
    }


def _extract_overfitting_fields(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = _lookup_dict("overfitting_diagnostics", summary_seed, metric_summary, pipeline_metadata)
    fields = [
        "train_MAE",
        "val_MAE",
        "holdout_MAE",
        "train_sign_acc",
        "train_sign_acc_pct",
        "val_sign_acc",
        "val_sign_acc_pct",
        "holdout_sign_acc",
        "holdout_sign_acc_pct",
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
    payload: dict[str, Any] = {"diagnostics": diagnostics}
    for field in fields:
        payload[field] = _lookup_first(
            diagnostics.get(field),
            _lookup_value(field, summary_seed, metric_summary, pipeline_metadata),
        )
    for acc_field, pct_field in [
        ("train_sign_acc", "train_sign_acc_pct"),
        ("val_sign_acc", "val_sign_acc_pct"),
        ("holdout_sign_acc", "holdout_sign_acc_pct"),
    ]:
        if payload.get(pct_field) is None:
            payload[pct_field] = _accuracy_pct(payload.get(acc_field))
    return payload


def _extract_sign_confusion_fields(
    metric_summary: dict[str, Any],
    direct_model: dict[str, Any],
    accuracy: dict[str, Any],
) -> dict[str, int | None]:
    sign_confusion = _lookup_dict("sign_confusion", direct_model, accuracy, metric_summary)
    return {
        "sign_tp": _safe_int(_lookup_first(sign_confusion.get("true_positive"), _lookup_value("sign_tp", direct_model, accuracy, metric_summary))),
        "sign_tn": _safe_int(_lookup_first(sign_confusion.get("true_negative"), _lookup_value("sign_tn", direct_model, accuracy, metric_summary))),
        "sign_fp": _safe_int(_lookup_first(sign_confusion.get("false_positive"), _lookup_value("sign_fp", direct_model, accuracy, metric_summary))),
        "sign_fn": _safe_int(_lookup_first(sign_confusion.get("false_negative"), _lookup_value("sign_fn", direct_model, accuracy, metric_summary))),
    }


def _extract_raw_model_metrics(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    metrics = _lookup_dict("raw_model_metrics", summary_seed, metric_summary, pipeline_metadata)
    raw_keys = [
        "raw_model_MAE",
        "raw_model_sign_acc",
        "raw_model_sign_acc_pct",
        "raw_model_direction_acc",
        "raw_model_direction_acc_pct",
        "raw_model_delta_vs_baseline",
        "raw_model_mean_delta_vs_baseline",
        "raw_model_std_delta_vs_baseline",
        "raw_model_win_rate_vs_baseline",
    ]
    for key in raw_keys:
        if key not in metrics:
            metrics[key] = _lookup_value(key, summary_seed, metric_summary, pipeline_metadata)
    return metrics


def _extract_selection_fields(
    summary_seed: dict[str, Any],
    metric_summary: dict[str, Any],
    pipeline_metadata: dict[str, Any],
) -> dict[str, Any]:
    direct_strategy = _lookup_dict("direct_strategy", metric_summary, pipeline_metadata, summary_seed)
    selection = {
        "selected_candidate_type": str(_lookup_first(direct_strategy.get("type"), "model_only")),
        "main_selection_candidate_type": _lookup_value(
            "main_selection_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "main_selection_final_ranking_reason": _lookup_value(
            "main_selection_final_ranking_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "main_selection_relaxed_rule_applied": bool(
            _lookup_first(
                _lookup_value(
                    "main_selection_relaxed_rule_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "main_selection_baseline_overridden": bool(
            _lookup_first(
                _lookup_value(
                    "main_selection_baseline_overridden",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "main_persistence_promotion_applied": bool(
            _lookup_first(
                _lookup_value(
                    "main_persistence_promotion_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "final_holdout_guard_reason": _lookup_value(
            "final_holdout_guard_reason",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "final_holdout_guard_applied": bool(
            _lookup_first(
                _lookup_value(
                    "final_holdout_guard_applied",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "raw_model_candidate_type": _lookup_value(
            "raw_model_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "raw_model_used_before_guard": bool(
            _lookup_first(
                _lookup_value(
                    "raw_model_used_before_guard",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                True,
            )
        ),
        "guarded_candidate_type": _lookup_value(
            "guarded_candidate_type",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "guarded_candidate_after_guard": bool(
            _lookup_first(
                _lookup_value(
                    "guarded_candidate_after_guard",
                    summary_seed,
                    metric_summary,
                    pipeline_metadata,
                ),
                False,
            )
        ),
        "final_holdout_candidate_before_guard": _lookup_dict(
            "final_holdout_candidate_before_guard",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "final_holdout_candidate_after_guard": _lookup_dict(
            "final_holdout_candidate_after_guard",
            summary_seed,
            metric_summary,
            pipeline_metadata,
        ),
        "validation_mae": _safe_float(direct_strategy.get("validation_mae")),
        "selection_pool": direct_strategy.get("selection_pool"),
        "composition_profile": direct_strategy.get("composition_profile"),
        "profile_selection_mode": direct_strategy.get("profile_selection_mode"),
        "profile_evaluations": list(direct_strategy.get("profile_evaluations", []) or []),
        "main_selection_relaxed_rule": dict(direct_strategy.get("main_selection_relaxed_rule", {}) or {}),
        "main_persistence_promotion": dict(direct_strategy.get("main_persistence_promotion", {}) or {}),
        "direct_strategy": direct_strategy,
    }
    if selection["main_selection_candidate_type"] is None:
        selection["main_selection_candidate_type"] = selection["selected_candidate_type"]
    if selection["raw_model_candidate_type"] is None:
        selection["raw_model_candidate_type"] = "model_only"
    if selection["guarded_candidate_type"] is None:
        selection["guarded_candidate_type"] = selection["main_selection_candidate_type"]
    return selection


def _build_model_record(
    *,
    outputs_dir: Path,
    model_key: str,
    summary_seed: Optional[dict[str, Any]],
    pipeline_summary: Optional[dict[str, Any]],
) -> dict[str, Any]:
    summary_seed = dict(summary_seed or {})
    paths = _model_artifact_paths(outputs_dir, model_key)
    backtest_summary = dict(_safe_read_json(paths["backtest_summary"]) or {})
    pipeline_metadata = dict(_safe_read_json(paths["pipeline_metadata"]) or {})
    multi_window_summary = dict(_safe_read_json(paths["multi_window_summary"]) or {})
    comparison_vs_baselines = dict(_safe_read_json(paths["comparison_vs_baselines"]) or {})

    metric_summary = dict(backtest_summary or summary_seed.get("metrics", {}) or summary_seed.get("backtest_summary", {}) or {})
    direct_model = dict(metric_summary.get("direct_model", {}) or {})
    accuracy = dict(metric_summary.get("accuracy_metrics", {}) or {})
    robustness = _extract_robustness_metrics(summary_seed, metric_summary, pipeline_metadata, multi_window_summary)
    raw_model_metrics = _extract_raw_model_metrics(summary_seed, metric_summary, pipeline_metadata)
    selection = _extract_selection_fields(summary_seed, metric_summary, pipeline_metadata)
    overfitting = _extract_overfitting_fields(summary_seed, metric_summary, pipeline_metadata)

    sign_acc = _safe_float(_lookup_first(direct_model.get("sign_accuracy"), accuracy.get("sign_accuracy")))
    sign_acc_pct = _safe_float(
        _lookup_first(
            metric_summary.get("sign_accuracy_pct"),
            direct_model.get("sign_accuracy_pct"),
            accuracy.get("sign_accuracy_pct"),
        )
    )
    sign_confusion = _extract_sign_confusion_fields(metric_summary, direct_model, accuracy)
    direction_acc = _safe_float(_lookup_first(accuracy.get("direction_accuracy"), metric_summary.get("direction_accuracy")))
    direction_acc_pct = _safe_float(
        _lookup_first(
            metric_summary.get("direction_accuracy_pct"),
            accuracy.get("direction_accuracy_pct"),
        )
    )
    summary = {
        "MAE": _safe_float(direct_model.get("MAE")),
        "RMSE": _safe_float(direct_model.get("RMSE")),
        "MAPE": _safe_float(direct_model.get("MAPE")),
        "return_MAE": _safe_float(direct_model.get("return_MAE")),
        "sign_acc": sign_acc,
        "sign_acc_pct": sign_acc_pct,
        **sign_confusion,
        "direction_acc": direction_acc,
        "direction_acc_pct": direction_acc_pct,
        "delta_vs_baseline": _extract_guarded_delta(metric_summary),
        "robustness_status": robustness.get("robustness_status"),
        "selection_eligibility": robustness.get("selection_eligibility"),
        "overfit_status": overfitting.get("overfit_status"),
        "overfit_reason": overfitting.get("overfit_reason"),
    }

    registry_row = {
        "model_key": model_key,
        "model_name": _display_model_name(model_key),
        "is_main": model_key == "main_direct_pipeline",
        "robustness_status": summary["robustness_status"],
        "selection_eligibility": summary["selection_eligibility"],
        "delta_vs_baseline": summary["delta_vs_baseline"],
        "mean_delta_vs_baseline": robustness.get("mean_delta_vs_baseline"),
        "std_delta_vs_baseline": robustness.get("std_delta_vs_baseline"),
        "win_rate_vs_baseline": robustness.get("win_rate_vs_baseline"),
        "sign_acc_pct": summary["sign_acc_pct"],
        "sign_tp": summary.get("sign_tp"),
        "sign_tn": summary.get("sign_tn"),
        "sign_fp": summary.get("sign_fp"),
        "sign_fn": summary.get("sign_fn"),
        "direction_acc_pct": summary["direction_acc_pct"],
        "overfit_status": summary["overfit_status"],
        "overfit_reason": summary["overfit_reason"],
        "raw_model_delta_vs_baseline": raw_model_metrics.get("raw_model_delta_vs_baseline"),
        "raw_model_sign_acc_pct": raw_model_metrics.get("raw_model_sign_acc_pct"),
        "raw_model_direction_acc_pct": raw_model_metrics.get("raw_model_direction_acc_pct"),
        "raw_model_candidate_type": selection.get("raw_model_candidate_type"),
        "raw_model_used_before_guard": selection.get("raw_model_used_before_guard"),
        "guarded_candidate_type": selection.get("guarded_candidate_type"),
        "guarded_candidate_after_guard": selection.get("guarded_candidate_after_guard"),
        "recommendation_bucket": _lookup_value("recommendation_bucket", summary_seed, metric_summary, pipeline_metadata),
    }
    registry_row["recommendation_bucket"] = _derive_recommendation_bucket(registry_row)

    return {
        "model_key": model_key,
        "model_name": _display_model_name(model_key),
        "is_main": model_key == "main_direct_pipeline",
        "summary": summary,
        "raw_model": raw_model_metrics,
        "overfitting": overfitting,
        "robustness": robustness,
        "selection": selection,
        "registry": registry_row,
        "artifact_paths": {name: str(path) for name, path in paths.items()},
        "artifacts": {
            "pipeline_summary_entry": summary_seed,
            "pipeline_summary": dict(pipeline_summary or {}),
            "backtest_summary": backtest_summary,
            "pipeline_metadata": pipeline_metadata,
            "multi_window_summary": multi_window_summary,
            "comparison_vs_baselines": comparison_vs_baselines,
        },
    }


def _collect_model_keys(outputs_dir: Path, pipeline_summary: Optional[dict[str, Any]]) -> list[str]:
    keys = {"main_direct_pipeline"}
    pipeline_multi = dict((pipeline_summary or {}).get("multi_models", {}) or {})
    keys.update(str(key) for key in pipeline_multi.keys())

    for base_dir in [outputs_dir / "backtest_results" / "multi_models", outputs_dir / "reports" / "multi_models"]:
        if not base_dir.exists():
            continue
        for child in base_dir.iterdir():
            if child.is_dir():
                keys.add(child.name)

    return ["main_direct_pipeline"] + sorted(key for key in keys if key != "main_direct_pipeline")


def _build_model_records(outputs_dir: Path, pipeline_summary: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    keys = _collect_model_keys(outputs_dir, pipeline_summary)
    pipeline_multi = dict((pipeline_summary or {}).get("multi_models", {}) or {})
    records: dict[str, dict[str, Any]] = {}
    for key in keys:
        summary_seed = dict(pipeline_summary or {}) if key == "main_direct_pipeline" else dict(pipeline_multi.get(key, {}) or {})
        records[key] = _build_model_record(
            outputs_dir=outputs_dir,
            model_key=key,
            summary_seed=summary_seed,
            pipeline_summary=pipeline_summary,
        )
    return records


@st.cache_data(show_spinner=False)
def list_market_files() -> list[str]:
    if not ARTIFACTS_DIR.exists():
        return []
    files = sorted(path.name for path in ARTIFACTS_DIR.glob("*_market_dataset.csv"))
    if files:
        return files
    return sorted(path.name for path in ARTIFACTS_DIR.glob("*_klines.csv"))


@st.cache_data(show_spinner=False)
def load_market_data(file_name: Optional[str] = None) -> pd.DataFrame:
    files = list_market_files()
    if not files:
        return pd.DataFrame()
    chosen = file_name or files[-1]
    df = _safe_read_csv(ARTIFACTS_DIR / chosen)
    for col in df.columns:
        if col != "timestamp":
            try:
                df[col] = pd.to_numeric(df[col])
            except Exception:
                continue
    if "timestamp" in df.columns:
        return df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    return df


@st.cache_data(show_spinner=False)
def load_anomalies() -> pd.DataFrame:
    return _safe_read_csv(LOG_DIR / "anomaly_flags.csv")


@st.cache_data(show_spinner=False)
def load_anomaly_windows() -> pd.DataFrame:
    return _safe_read_csv(LOG_DIR / "anomaly_windows.csv")


@st.cache_data(show_spinner=False)
def load_backtest_results(model_key: Optional[str] = None) -> pd.DataFrame:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_csv(paths["backtest_results"])


@st.cache_data(show_spinner=False)
def load_backtest_summary(model_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_json(paths["backtest_summary"])


@st.cache_data(show_spinner=False)
def load_pipeline_metadata(model_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_json(paths["pipeline_metadata"])


@st.cache_data(show_spinner=False)
def load_multi_window_summary(model_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_json(paths["multi_window_summary"])


@st.cache_data(show_spinner=False)
def load_comparison_vs_baselines(model_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_json(paths["comparison_vs_baselines"])


@st.cache_data(show_spinner=False)
def load_live_snapshot() -> Optional[dict[str, Any]]:
    return _safe_read_json(LOG_DIR / "latest_live_prediction.json")


@st.cache_data(show_spinner=False)
def load_pipeline_summary() -> Optional[dict[str, Any]]:
    return _safe_read_json(REPORT_DIR / "pipeline_summary.json")


@st.cache_data(show_spinner=False)
def load_feature_importance(model_key: Optional[str] = None) -> Optional[dict[str, Any]]:
    key = model_key or "main_direct_pipeline"
    paths = _model_artifact_paths(OUTPUTS_DIR, key)
    return _safe_read_json(paths["feature_importance"])


@st.cache_data(show_spinner=False)
def list_model_keys() -> list[str]:
    pipeline_summary = load_pipeline_summary()
    return _collect_model_keys(OUTPUTS_DIR, pipeline_summary)


@st.cache_data(show_spinner=False)
def load_model_records() -> dict[str, dict[str, Any]]:
    pipeline_summary = load_pipeline_summary()
    return _build_model_records(OUTPUTS_DIR, pipeline_summary)


@st.cache_data(show_spinner=False)
def load_model_record(model_key: str) -> Optional[dict[str, Any]]:
    return dict(load_model_records().get(model_key, {}) or {}) or None


@st.cache_data(show_spinner=False)
def load_model_registry() -> pd.DataFrame:
    records = load_model_records()
    rows = [dict(record.get("registry", {}) or {}) for record in records.values() if record.get("registry")]
    if not rows:
        return pd.DataFrame()
    registry = pd.DataFrame(rows)
    sort_cols = []
    ascending = []
    if "selection_eligibility" in registry.columns:
        sort_cols.append("selection_eligibility")
        ascending.append(False)
    if "delta_vs_baseline" in registry.columns:
        sort_cols.append("delta_vs_baseline")
        ascending.append(False)
    if "mean_delta_vs_baseline" in registry.columns:
        sort_cols.append("mean_delta_vs_baseline")
        ascending.append(False)
    if sort_cols:
        registry = registry.sort_values(by=sort_cols, ascending=ascending, na_position="last")
    return registry.reset_index(drop=True)


def get_latest_prediction(backtest_df: pd.DataFrame) -> Optional[dict[str, Any]]:
    if backtest_df.empty:
        return None
    return backtest_df.dropna(how="all").iloc[-1].to_dict()


def compute_market_summary(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}
    out = {
        "last_close": float(df["close"].iloc[-1]),
        "prev_close": float(df["close"].iloc[-2]) if len(df) > 1 else float(df["close"].iloc[-1]),
        "last_volume": float(df["volume"].iloc[-1]) if "volume" in df.columns else None,
        "rows": int(len(df)),
        "mark_div": float(((df["close"] - df["mark_close"]) / (df["close"] + 1e-8)).iloc[-1]) if "mark_close" in df.columns else None,
    }
    out["change_1h"] = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-13]) - 1.0) if len(df) >= 13 else None
    out["change_24h"] = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-289]) - 1.0) if len(df) >= 289 else None
    returns = df["close"].pct_change().dropna()
    out["volatility_1h"] = float(returns.tail(12).std()) if len(returns) >= 12 else None
    return out