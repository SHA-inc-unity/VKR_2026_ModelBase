from __future__ import annotations

import os

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    BACKTEST_DIR,
    DIRECTION_DEADBAND,
    ENABLE_MULTI_WINDOW_EVALUATION,
    EVALUATION_WINDOW_COUNT,
    EVALUATION_WINDOW_SIZE,
    EVALUATION_WINDOW_STEP,
    MODEL_DIR,
    REPORT_DIR,
    TRAIN_LOOKBACK_DAYS,
)
from catboost_floader.core.utils import ensure_dirs, get_logger, save_json
from catboost_floader.data.ingestion import assemble_market_dataset
from catboost_floader.data.preprocessing import preprocess_data
from catboost_floader.features.engineering import build_direct_features, build_range_features
from catboost_floader.targets.generation import generate_direct_targets, generate_range_targets

from catboost_floader.app.multi_model_orchestration import _run_multi_models
from catboost_floader.app.pipeline_execution import _run_pipeline_bundle
from catboost_floader.app.pipeline_preparation import _prepare_pipeline_splits, _tune_pipeline_models
from catboost_floader.app.pipeline_summary import _build_pipeline_summary
from catboost_floader.selection.composition_profiles import _main_direct_composition_profile

logger = get_logger("app_main")


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

    # Log distribution of generated direction labels (for debugging).
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
        model_key="main_direct_pipeline",
        enable_multi_window_evaluation=ENABLE_MULTI_WINDOW_EVALUATION,
        evaluation_window_count=EVALUATION_WINDOW_COUNT,
        evaluation_window_size=EVALUATION_WINDOW_SIZE,
        evaluation_window_step=EVALUATION_WINDOW_STEP,
        model_dir=MODEL_DIR,
        report_dir=REPORT_DIR,
        backtest_dir=BACKTEST_DIR,
    )

    # Live inference is intentionally disabled for now:
    # - it is orthogonal to backtest quality
    # - it can fail due to runtime data issues / API drift
    live_result = {"status": "skipped"}

    multi_models_summary = _run_multi_models(raw)

    summary = _build_pipeline_summary(
        prepared_main=prepared_main,
        main_result=main_result,
        multi_models_summary=multi_models_summary,
        live_result=live_result,
    )

    save_json(summary, os.path.join(REPORT_DIR, "pipeline_summary.json"))
    print("Done")


if __name__ == "__main__":
    main()
