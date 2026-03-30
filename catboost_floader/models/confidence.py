from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from catboost_floader.core.config import MODEL_DIR, OOD_ZSCORE_CLIP
from catboost_floader.core.utils import ensure_dirs, load_pickle, save_pickle


@dataclass
class ErrorCalibrator:
    model: RandomForestRegressor | None = None
    feature_names: list[str] | None = None

    def fit(self, X: pd.DataFrame, abs_error: pd.Series) -> "ErrorCalibrator":
        self.feature_names = list(X.columns)
        self.model = RandomForestRegressor(n_estimators=120, random_state=42, n_jobs=1, max_depth=6)
        self.model.fit(X[self.feature_names], abs_error)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.model is None or self.feature_names is None:
            raise RuntimeError("ErrorCalibrator is not fitted")
        X2 = X.copy()
        for col in list(X2.columns):
            dt = str(X2[col].dtype)
            if dt == "object" or dt.startswith("string") or "datetime" in dt:
                X2 = X2.drop(columns=[col], errors="ignore")
        X2 = X2.reindex(columns=self.feature_names, fill_value=0.0)
        return self.model.predict(X2)

    def save(self, path: str) -> None:
        ensure_dirs([os.path.dirname(path)])
        save_pickle(self, path)

    @staticmethod
    def load(path: str) -> "ErrorCalibrator":
        return load_pickle(path)


def fit_error_calibrator(
    X_train: pd.DataFrame,
    direct_pred_return: pd.Series,
    y_train_return: pd.Series,
    save_path: str | None = None,
) -> ErrorCalibrator:
    abs_error = (pd.Series(direct_pred_return).reset_index(drop=True) - pd.Series(y_train_return).reset_index(drop=True)).abs()
    calibrator = ErrorCalibrator().fit(X_train.reset_index(drop=True), abs_error)
    calibrator.save(save_path or os.path.join(MODEL_DIR, "error_calibrator.pkl"))
    return calibrator


def compute_ood_score(row: pd.Series, feature_stats: dict[str, dict[str, float]]) -> float:
    zscores = []
    for feature, stats in feature_stats.items():
        if feature not in row.index:
            continue
        std = float(stats.get("std", 0.0))
        if std <= 1e-8:
            continue
        z = abs((float(row[feature]) - float(stats.get("mean", 0.0))) / std)
        zscores.append(min(z, OOD_ZSCORE_CLIP))
    if not zscores:
        return 0.0
    return float(np.clip(np.mean(zscores) / OOD_ZSCORE_CLIP, 0.0, 1.0))


def resolve_array_backend(prefer_gpu: bool = False) -> Tuple[Any, str]:
    if prefer_gpu:
        try:
            import cupy as cp

            return cp, "gpu"
        except Exception:
            pass
    return np, "cpu"


def compute_ood_scores_batch(
    X: pd.DataFrame,
    feature_stats: dict[str, dict[str, float]],
    *,
    prefer_gpu: bool = False,
    xp: Any | None = None,
) -> tuple[np.ndarray, str]:
    xp_mod, backend = (xp, "gpu" if getattr(xp, "__name__", "") == "cupy" else "cpu") if xp is not None else resolve_array_backend(prefer_gpu)
    feature_names = [feature for feature, stats in feature_stats.items() if float(stats.get("std", 0.0)) > 1e-8]
    if not feature_names:
        return np.zeros(len(X), dtype=float), backend

    X_num = X.reindex(columns=feature_names, fill_value=np.nan).apply(pd.to_numeric, errors="coerce")
    arr = X_num.to_numpy(dtype=float, copy=True)
    means = np.asarray([float(feature_stats[feature].get("mean", 0.0)) for feature in feature_names], dtype=float)
    stds = np.asarray([float(feature_stats[feature].get("std", 1.0)) for feature in feature_names], dtype=float)
    arr = np.where(np.isnan(arr), means.reshape(1, -1), arr)

    if xp_mod is np:
        z = np.abs((arr - means.reshape(1, -1)) / stds.reshape(1, -1))
        out = np.clip(np.mean(np.clip(z, 0.0, OOD_ZSCORE_CLIP), axis=1) / OOD_ZSCORE_CLIP, 0.0, 1.0)
        return out.astype(float), backend

    arr_x = xp_mod.asarray(arr)
    means_x = xp_mod.asarray(means)
    stds_x = xp_mod.asarray(stds)
    z = xp_mod.abs((arr_x - means_x.reshape(1, -1)) / stds_x.reshape(1, -1))
    out = xp_mod.clip(xp_mod.mean(xp_mod.clip(z, 0.0, OOD_ZSCORE_CLIP), axis=1) / OOD_ZSCORE_CLIP, 0.0, 1.0)
    return xp_mod.asnumpy(out).astype(float), backend


def compute_confidence(predicted_abs_error: float, anomaly_score: float, ood_score: float, band_width_norm: float) -> float:
    raw = 1.0 - 1.2 * predicted_abs_error - 0.5 * anomaly_score - 0.4 * ood_score - 0.8 * band_width_norm
    return float(np.clip(raw, 0.0, 1.0))


def compute_confidence_batch(
    predicted_abs_error: np.ndarray,
    anomaly_score: np.ndarray,
    ood_score: np.ndarray,
    band_width_norm: np.ndarray,
    *,
    prefer_gpu: bool = False,
    xp: Any | None = None,
) -> tuple[np.ndarray, str]:
    xp_mod, backend = (xp, "gpu" if getattr(xp, "__name__", "") == "cupy" else "cpu") if xp is not None else resolve_array_backend(prefer_gpu)
    pae = np.asarray(predicted_abs_error, dtype=float)
    ano = np.asarray(anomaly_score, dtype=float)
    ood = np.asarray(ood_score, dtype=float)
    band = np.asarray(band_width_norm, dtype=float)

    if xp_mod is np:
        raw = 1.0 - 1.2 * pae - 0.5 * ano - 0.4 * ood - 0.8 * band
        return np.clip(raw, 0.0, 1.0).astype(float), backend

    raw = (
        1.0
        - 1.2 * xp_mod.asarray(pae)
        - 0.5 * xp_mod.asarray(ano)
        - 0.4 * xp_mod.asarray(ood)
        - 0.8 * xp_mod.asarray(band)
    )
    return xp_mod.asnumpy(xp_mod.clip(raw, 0.0, 1.0)).astype(float), backend
