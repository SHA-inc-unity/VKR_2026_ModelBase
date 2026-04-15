from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

from catboost_floader.core.config import REPORT_DIR
from catboost_floader.diagnostics.artifact_registry import model_artifact_paths


def _safe_read_json(path: str) -> dict[str, Any] | None:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe_read_csv(path: str, *, parse_timestamps: bool = True) -> pd.DataFrame:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if parse_timestamps and "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    return frame


def load_pipeline_summary() -> dict[str, Any] | None:
    return _safe_read_json(os.path.join(REPORT_DIR, "pipeline_summary.json"))


def load_model_backtest_summary(model_key: str | None = None) -> dict[str, Any] | None:
    return _safe_read_json(model_artifact_paths(model_key)["backtest_summary"])


def load_model_pipeline_metadata(model_key: str | None = None) -> dict[str, Any] | None:
    return _safe_read_json(model_artifact_paths(model_key)["pipeline_metadata"])


def load_model_multi_window_summary(model_key: str | None = None) -> dict[str, Any] | None:
    return _safe_read_json(model_artifact_paths(model_key)["multi_window_summary"])


def load_model_comparison_vs_baselines(model_key: str | None = None) -> dict[str, Any] | None:
    return _safe_read_json(model_artifact_paths(model_key)["comparison_vs_baselines"])


def load_model_feature_importance(model_key: str | None = None) -> dict[str, Any] | None:
    return _safe_read_json(model_artifact_paths(model_key)["feature_importance"])


def load_model_backtest_results(model_key: str | None = None) -> pd.DataFrame:
    return _safe_read_csv(model_artifact_paths(model_key)["backtest_results"])


def load_model_raw_predictions(model_key: str | None = None) -> pd.DataFrame:
    return _safe_read_csv(model_artifact_paths(model_key)["raw_predictions"])