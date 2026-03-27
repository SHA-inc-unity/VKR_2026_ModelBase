from __future__ import annotations

import os
from dataclasses import dataclass

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


def fit_error_calibrator(X_train: pd.DataFrame, direct_pred_return: pd.Series, y_train_return: pd.Series) -> ErrorCalibrator:
    abs_error = (pd.Series(direct_pred_return).reset_index(drop=True) - pd.Series(y_train_return).reset_index(drop=True)).abs()
    calibrator = ErrorCalibrator().fit(X_train.reset_index(drop=True), abs_error)
    calibrator.save(os.path.join(MODEL_DIR, "error_calibrator.pkl"))
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


def compute_confidence(predicted_abs_error: float, anomaly_score: float, ood_score: float, band_width_norm: float) -> float:
    raw = 1.0 - 1.2 * predicted_abs_error - 0.5 * anomaly_score - 0.4 * ood_score - 0.8 * band_width_norm
    return float(np.clip(raw, 0.0, 1.0))
