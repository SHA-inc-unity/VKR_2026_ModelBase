from __future__ import annotations

import argparse
import re
from typing import Any

from catboost_floader.core.config import (
    BACKTEST_DIR,
    ENABLE_MULTI_WINDOW_EVALUATION,
    EVALUATION_WINDOW_COUNT,
    EVALUATION_WINDOW_SIZE,
    EVALUATION_WINDOW_STEP,
    MODEL_DIR,
    MULTI_SKIP_TUNING,
    REPORT_DIR,
    TRAIN_LOOKBACK_DAYS,
)
from catboost_floader.core.utils import ensure_dirs, get_logger
from catboost_floader.data.ingestion import assemble_market_dataset
from catboost_floader.data.preprocessing import aggregate_for_modeling, preprocess_data
from catboost_floader.features.engineering import build_direct_features, build_range_features
from catboost_floader.targets.generation import generate_direct_targets, generate_range_targets

from catboost_floader.app.main import main as run_full_pipeline
from catboost_floader.app.multi_model_task import _run_multi_model_key_task
from catboost_floader.app.pipeline_execution import _run_pipeline_bundle
from catboost_floader.app.pipeline_preparation import _prepare_pipeline_splits, _tune_pipeline_models
from catboost_floader.diagnostics.artifact_registry import _multi_model_artifact_paths
from catboost_floader.selection.composition_profiles import _main_direct_composition_profile

logger = get_logger("job_entrypoints")

_MULTI_MODEL_KEY_RE = re.compile(r"^(?P<timeframe>\d+)min_(?P<hours>\d+)h$")


def run_all_models_job() -> None:
    logger.info("Running full pipeline job entrypoint.")
    run_full_pipeline()


def run_selected_model_job(model_key: str) -> dict[str, Any]:
    normalized_key = str(model_key or "").strip()
    if not normalized_key:
        raise ValueError("Model key is required for selected-model execution.")

    ensure_dirs([BACKTEST_DIR, MODEL_DIR, REPORT_DIR])
    raw = assemble_market_dataset(lookback_days=TRAIN_LOOKBACK_DAYS)
    if normalized_key == "main_direct_pipeline":
        return _run_main_pipeline_only(raw)
    return _run_multi_model_only(raw, normalized_key)


def _run_main_pipeline_only(raw: Any) -> dict[str, Any]:
    print("Loading selected main pipeline inputs...")
    clean = preprocess_data(raw)
    direct_features = build_direct_features(clean)
    range_features = build_range_features(clean)
    direct_targets = generate_direct_targets(clean)
    range_targets = generate_range_targets(clean)
    prepared_main = _prepare_pipeline_splits(
        direct_features,
        range_features,
        direct_targets,
        range_targets,
        persist_anomalies=True,
    )
    tuned_main = _tune_pipeline_models(prepared_main)
    result = _run_pipeline_bundle(
        prepared_main,
        direct_params=tuned_main["direct"],
        range_low_params=tuned_main["range_low"],
        range_high_params=tuned_main["range_high"],
        direct_composition_profile=_main_direct_composition_profile(),
        model_key="main_direct_pipeline",
        enable_multi_window_evaluation=ENABLE_MULTI_WINDOW_EVALUATION,
        evaluation_window_count=EVALUATION_WINDOW_COUNT,
        evaluation_window_size=EVALUATION_WINDOW_SIZE,
        evaluation_window_step=EVALUATION_WINDOW_STEP,
        model_dir=MODEL_DIR,
        report_dir=REPORT_DIR,
        backtest_dir=BACKTEST_DIR,
    )
    return {
        "model_key": "main_direct_pipeline",
        "summary": "Selected main pipeline completed.",
        "backtest_points": dict(result.get("backtest_summary", {}) or {}).get("backtest_points"),
        "direction_points": dict(result.get("backtest_summary", {}) or {}).get("direction_points"),
    }


def _run_multi_model_only(raw: Any, model_key: str) -> dict[str, Any]:
    match = _MULTI_MODEL_KEY_RE.match(model_key)
    if not match:
        raise ValueError(f"Unsupported model key format: {model_key}")

    timeframe = int(match.group("timeframe"))
    hours = int(match.group("hours"))
    steps = int((hours * 60) // timeframe)
    if steps < 1:
        raise ValueError(f"Invalid multi-model horizon for key: {model_key}")

    print(f"Preparing selected multi-model {model_key}...")
    raw_prep = raw.copy()
    df_tf = aggregate_for_modeling(raw_prep, timeframe)
    direct_features = build_direct_features(df_tf)
    range_features = build_range_features(df_tf)
    task = {
        "key": model_key,
        "tf": timeframe,
        "hours": hours,
        "steps": steps,
        "df_tf": df_tf,
        "direct_features": direct_features,
        "range_features": range_features,
        "skip_tuning": MULTI_SKIP_TUNING,
    }
    result = _run_multi_model_key_task(task)
    if result.get("status") != "ok":
        raise RuntimeError(result.get("message") or f"Selected multi-model run failed for {model_key}.")
    summary = dict(result.get("summary", {}) or {})
    return {
        "model_key": model_key,
        "summary": f"Selected model {model_key} completed.",
        "rows": summary.get("rows"),
        "artifacts": _multi_model_artifact_paths(model_key),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backend job entrypoints for the ModelLine dashboard.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run-all-models", help="Run the full model pipeline.")

    selected_parser = subparsers.add_parser("run-selected-model", help="Run a single selected model.")
    selected_parser.add_argument("--model-key", required=True, help="Model key to execute.")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "run-all-models":
        run_all_models_job()
        return
    if args.command == "run-selected-model":
        run_selected_model_job(args.model_key)
        return
    raise ValueError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()