from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from catboost_floader.core.config import MODEL_DIR, DIRECT_CATBOOST_PARAMS, RANDOM_SEED, apply_hardware_params, DIRECTION_DEADBAND
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json

logger = get_logger("model_direction")


class DirectionModel:
    def __init__(self, params: Optional[Dict[str, Any]] = None, label_map: Optional[Dict[int, int]] = None):
        # params expected to be CatBoost-compatible for classification
        self.model: Optional[CatBoostClassifier] = None
        self.params = params or {**DIRECT_CATBOOST_PARAMS}
        # override to classification defaults
        self.params.setdefault("loss_function", "Logloss")
        self.params.setdefault("eval_metric", "AUC")
        self.feature_names: list[str] = []
        # mapping between original sign labels and class indices (e.g. {-1:0, 0:1, 1:2})
        self.label_map = label_map or {-1: 0, 0: 1, 1: 2}
        # reverse map cached
        self._rev_map = {v: k for k, v in self.label_map.items()}

    def _prepare_labels(self, y: pd.Series) -> pd.Series:
        # Convert continuous returns into sign classes: -1, 0, 1
        y_num = pd.to_numeric(y, errors="coerce").fillna(0.0).to_numpy(dtype=float)
        # apply deadband: small returns treated as zero to reduce label noise
        dead = float(DIRECTION_DEADBAND)
        signs = np.sign(y_num)
        signs[np.abs(y_num) < dead] = 0.0
        # map -1.0/0.0/1.0 to discrete indices
        mapped = np.array([self.label_map.get(int(s), self.label_map.get(0)) for s in signs], dtype=int)
        return pd.Series(mapped)

    def fit(self, X: pd.DataFrame, y: pd.Series):
        # prepare labels first so we can decide binary vs multiclass objective
        y_labels = self._prepare_labels(y)

        params = dict(self.params)
        params["random_seed"] = RANDOM_SEED

        # if labels contain more than 2 unique values, switch to multiclass loss
        unique_vals = np.unique(y_labels.to_numpy() if isinstance(y_labels, pd.Series) else y_labels)
        if len(unique_vals) > 2:
            # explicitly set multiclass objective when >2 classes present
            params["loss_function"] = "MultiClass"
            # prefer F1/accuracy for multiclass evaluation
            params["eval_metric"] = "TotalF1"
            logger.info("DirectionModel detected multiclass labels; using MultiClass loss")
        else:
            # leave provided loss (default is Logloss), but ensure sensible eval metric
            params.setdefault("loss_function", "Logloss")
            params.setdefault("eval_metric", "AUC")

        params = apply_hardware_params(params)
        # Use only numeric columns for training; drop categorical/text columns
        X_num = X.select_dtypes(include=[np.number]).copy()
        if X_num.empty:
            raise RuntimeError("No numeric features available to train DirectionModel")
        self.feature_names = list(X_num.columns)

        self.model = CatBoostClassifier(**params)
        self.model.fit(X_num, y_labels, verbose=False)
        logger.info("Trained DirectionModel")
        return self

    def _prepare_inference_frame(self, X: pd.DataFrame) -> pd.DataFrame:
        X2 = X.copy()
        for col in list(X2.columns):
            dt = str(X2[col].dtype)
            if dt == "object" or dt.startswith("string") or "datetime" in dt:
                X2 = X2.drop(columns=[col], errors="ignore")
        return X2

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("DirectionModel is not trained or loaded")
        X_orig = self._prepare_inference_frame(X)
        X_use = X_orig.reindex(columns=self.feature_names, fill_value=0.0) if self.feature_names else X_orig
        probs = self.model.predict_proba(X_use)
        return np.asarray(probs, dtype=float)

    def predict_sign_expectation(self, X: pd.DataFrame) -> np.ndarray:
        # returns expected sign in [-1,1]: E[sign] = p_pos*1 + p_zero*0 + p_neg*(-1)
        probs = self.predict_proba(X)
        # Align probability columns with actual class labels reported by the CatBoost model.
        # CatBoost may omit classes (e.g. no neutral class in training), so column order
        # must be read from `self.model.classes_` instead of assuming 0..n-1.
        if hasattr(self.model, "classes_"):
            class_labels = list(self.model.classes_)
        else:
            class_labels = list(range(probs.shape[1]))
        signs = np.array([self._rev_map.get(int(cl), 0) for cl in class_labels], dtype=float)
        return probs.dot(signs)

    def predict_label(self, X: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("DirectionModel is not trained or loaded")
        X_orig = self._prepare_inference_frame(X)
        X_use = X_orig.reindex(columns=self.feature_names, fill_value=0.0) if self.feature_names else X_orig
        preds = self.model.predict(X_use)
        # map back to -1/0/1
        return np.array([self._rev_map.get(int(p), 0) for p in preds], dtype=int)

    def save(self, prefix: str):
        if self.model is None:
            raise RuntimeError("DirectionModel is not trained")
        ensure_dirs([os.path.dirname(prefix)])
        self.model.save_model(prefix + "_direction.cbm")
        save_json(
            {"feature_names": self.feature_names, "params": self.params, "label_map": self.label_map},
            prefix + "_direction.json",
        )

    def load(self, prefix: str):
        self.model = CatBoostClassifier()
        self.model.load_model(prefix + "_direction.cbm")
        meta = load_json(prefix + "_direction.json") or {}
        self.feature_names = meta.get("feature_names", [])
        self.params = meta.get("params", self.params)
        self.label_map = meta.get("label_map", self.label_map)
        self._rev_map = {v: k for k, v in self.label_map.items()}
        return self


def train_direction_model(features: pd.DataFrame, targets: pd.DataFrame, params: Optional[Dict[str, Any]] = None, save: bool = True) -> DirectionModel:
    model = DirectionModel(params=params)
    # expect targets contains 'target_return'
    model.fit(features, targets["target_return"])
    if save:
        ensure_dirs([MODEL_DIR])
        model.save(os.path.join(MODEL_DIR, "direct_model"))
    return model
