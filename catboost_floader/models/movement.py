from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from catboost_floader.core.config import MODEL_DIR, DIRECT_CATBOOST_PARAMS, RANDOM_SEED, apply_hardware_params
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json

logger = get_logger("model_movement")


class MovementModel:
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.model: Optional[CatBoostRegressor] = None
        self.params = params or {**DIRECT_CATBOOST_PARAMS}
        self.feature_names: list[str] = []

    def fit(self, X: pd.DataFrame, y: pd.Series):
        # y expected to be continuous returns; we train on magnitude
        params = dict(self.params)
        params["random_seed"] = RANDOM_SEED
        params = apply_hardware_params(params)
        # Use only numeric features for training
        X_num = X.select_dtypes(include=[np.number]).copy()
        if X_num.empty:
            raise RuntimeError("No numeric features available to train MovementModel")
        self.feature_names = list(X_num.columns)
        y_mag = pd.to_numeric(y, errors="coerce").fillna(0.0).abs()
        self.model = CatBoostRegressor(**params)
        self.model.fit(X_num, y_mag, verbose=False)
        logger.info("Trained MovementModel")
        return self

    def _prepare_inference_frame(self, X: pd.DataFrame) -> pd.DataFrame:
        X2 = X.copy()
        for col in list(X2.columns):
            dt = str(X2[col].dtype)
            if dt == "object" or dt.startswith("string") or "datetime" in dt:
                X2 = X2.drop(columns=[col], errors="ignore")
        return X2

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("MovementModel is not trained or loaded")
        X_orig = self._prepare_inference_frame(X)
        X_use = X_orig.reindex(columns=self.feature_names, fill_value=0.0) if self.feature_names else X_orig
        preds = self.model.predict(X_use)
        return np.asarray(np.maximum(0.0, preds), dtype=float)

    def save(self, prefix: str):
        if self.model is None:
            raise RuntimeError("MovementModel is not trained")
        ensure_dirs([os.path.dirname(prefix)])
        self.model.save_model(prefix + "_movement.cbm")
        save_json({"feature_names": self.feature_names, "params": self.params}, prefix + "_movement.json")

    def load(self, prefix: str):
        self.model = CatBoostRegressor()
        self.model.load_model(prefix + "_movement.cbm")
        meta = load_json(prefix + "_movement.json") or {}
        self.feature_names = meta.get("feature_names", [])
        self.params = meta.get("params", self.params)
        return self


def train_movement_model(features: pd.DataFrame, targets: pd.DataFrame, params: Optional[Dict[str, Any]] = None, save: bool = True) -> MovementModel:
    model = MovementModel(params=params)
    model.fit(features, targets["target_return"])
    if save:
        ensure_dirs([MODEL_DIR])
        model.save(os.path.join(MODEL_DIR, "direct_model"))
    return model
