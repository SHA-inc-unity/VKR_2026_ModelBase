import json
import logging
import os
import pickle
from typing import Any, Iterable

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from catboost_floader.core.config import LOG_DIR


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    os.makedirs(LOG_DIR, exist_ok=True)
    stream_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, "catboost_system.log"), encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


def ensure_dirs(paths: Iterable[str]) -> None:
    for path in paths:
        os.makedirs(path, exist_ok=True)


def save_json(payload: dict[str, Any], path: str) -> None:
    ensure_dirs([os.path.dirname(path)])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


def load_json(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_pickle(obj: Any, path: str) -> None:
    ensure_dirs([os.path.dirname(path)])
    with open(path, "wb") as f:
        pickle.dump(obj, f)


def load_pickle(path: str) -> Any:
    with open(path, "rb") as f:
        return pickle.load(f)


def _feature_stats(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for col in df.columns:
        if col == "timestamp":
            continue
        ser = df[col]
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


def _drop_non_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    drop_cols = ["timestamp"]
    drop_cols += [c for c in out.columns if str(c).startswith("target_")]
    out = out.drop(columns=drop_cols, errors="ignore")
    numeric = out.select_dtypes(include=[np.number])
    return numeric.copy()


def _split_train_val(
    X: pd.DataFrame,
    y: pd.DataFrame,
    val_size: float = 0.15,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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


def _prepare_catboost_params(
    params: dict[str, Any] | None,
    default_params: dict[str, Any],
    thread_count: int | None,
) -> dict[str, Any] | None:
    if params is None and thread_count is None:
        return None
    out = dict(params) if params is not None else dict(default_params)
    out.pop("task_type", None)
    out.pop("devices", None)
    out.pop("gpu_ram_part", None)
    if thread_count is not None:
        out["thread_count"] = max(1, int(thread_count))
    return out
