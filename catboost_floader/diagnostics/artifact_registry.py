from __future__ import annotations

import os
from typing import Any

from catboost_floader.core.config import BACKTEST_DIR, MODEL_DIR, REPORT_DIR

MAIN_MODEL_KEY = "main_direct_pipeline"


def normalize_model_key(model_key: str | None) -> str:
    if model_key in (None, "", "default"):
        return MAIN_MODEL_KEY
    return str(model_key)


def is_main_model(model_key: str | None) -> bool:
    return normalize_model_key(model_key) == MAIN_MODEL_KEY


def artifact_roots() -> dict[str, str]:
    return {
        "model_dir": MODEL_DIR,
        "report_dir": REPORT_DIR,
        "backtest_dir": BACKTEST_DIR,
    }


def model_artifact_paths(model_key: str | None) -> dict[str, str]:
    key = normalize_model_key(model_key)
    if key == MAIN_MODEL_KEY:
        model_dir = MODEL_DIR
        report_dir = REPORT_DIR
        backtest_dir = BACKTEST_DIR
    else:
        model_dir = os.path.join(MODEL_DIR, "multi_models", key)
        report_dir = os.path.join(REPORT_DIR, "multi_models", key)
        backtest_dir = os.path.join(BACKTEST_DIR, "multi_models", key)

    return {
        "model_key": key,
        "is_main": key == MAIN_MODEL_KEY,
        "model_dir": model_dir,
        "report_dir": report_dir,
        "backtest_dir": backtest_dir,
        "direct_model": os.path.join(model_dir, "direct_model.json"),
        "range_model": os.path.join(model_dir, "range_model.json"),
        "feature_stats": os.path.join(model_dir, "feature_stats.json"),
        "feature_importance": os.path.join(report_dir, "feature_importance.json"),
        "backtest_summary": os.path.join(backtest_dir, "backtest_summary.json"),
        "backtest_results": os.path.join(backtest_dir, "backtest_results.csv"),
        "raw_predictions": os.path.join(backtest_dir, "raw_predictions.csv"),
        "baseline_outputs": os.path.join(backtest_dir, "baseline_outputs.csv"),
        "comparison_vs_baselines": os.path.join(backtest_dir, "comparison_vs_baselines.json"),
        "multi_window_metrics": os.path.join(backtest_dir, "multi_window_metrics.csv"),
        "multi_window_summary": os.path.join(backtest_dir, "multi_window_summary.json"),
        "direction_outputs": os.path.join(backtest_dir, "direction_outputs.csv"),
        "movement_outputs": os.path.join(backtest_dir, "movement_outputs.csv"),
        "pipeline_metadata": os.path.join(backtest_dir, "pipeline_metadata.json"),
    }


def _multi_model_artifact_paths(key: str) -> dict[str, str]:
    return model_artifact_paths(key)


def list_model_keys(pipeline_summary: dict[str, Any] | None = None) -> list[str]:
    keys = {MAIN_MODEL_KEY}
    pipeline_multi = dict((pipeline_summary or {}).get("multi_models", {}) or {})
    keys.update(str(key) for key in pipeline_multi.keys())

    for base_dir in (
        os.path.join(BACKTEST_DIR, "multi_models"),
        os.path.join(REPORT_DIR, "multi_models"),
    ):
        if not os.path.isdir(base_dir):
            continue
        for child in os.listdir(base_dir):
            child_path = os.path.join(base_dir, child)
            if os.path.isdir(child_path):
                keys.add(str(child))

    return [MAIN_MODEL_KEY] + sorted(key for key in keys if key != MAIN_MODEL_KEY)