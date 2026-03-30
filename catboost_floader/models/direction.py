from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from catboost_floader.core.config import MODEL_DIR, DIRECT_CATBOOST_PARAMS, RANDOM_SEED, apply_hardware_params, DIRECTION_DEADBAND
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix

logger = get_logger("model_direction")


class DirectionModel:
    @staticmethod
    def _normalize_label_map(label_map: Optional[Dict[int, int]]) -> Dict[int, int]:
        fallback = {-1: 0, 0: 1, 1: 2}
        if not label_map:
            return fallback
        normalized: Dict[int, int] = {}
        for k, v in label_map.items():
            try:
                normalized[int(k)] = int(v)
            except Exception:
                continue
        return normalized or fallback

    def __init__(self, params: Optional[Dict[str, Any]] = None, label_map: Optional[Dict[int, int]] = None):
        # params expected to be CatBoost-compatible for classification
        self.model: Optional[CatBoostClassifier] = None
        self.params = params or {**DIRECT_CATBOOST_PARAMS}
        # override to classification defaults
        self.params.setdefault("loss_function", "Logloss")
        self.params.setdefault("eval_metric", "AUC")
        self.feature_names: list[str] = []
        # mapping between original sign labels and class indices (e.g. {-1:0, 0:1, 1:2})
        self.label_map = self._normalize_label_map(label_map)
        # reverse map cached
        self._rev_map = {v: k for k, v in self.label_map.items()}
        self.is_degenerate: bool = False

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
        # compute numeric returns and true sign classes for diagnostics
        y_num = pd.to_numeric(y, errors="coerce").fillna(0.0).to_numpy(dtype=float)
        dead = float(DIRECTION_DEADBAND)
        true_signs = np.sign(y_num).astype(int)
        true_signs[np.abs(y_num) < dead] = 0

        y_labels = self._prepare_labels(y)

        params = dict(self.params)
        params["random_seed"] = RANDOM_SEED

        # Always train as multiclass over {-1,0,1} mapped to {0,1,2}.
        # This avoids objective mismatch and helps the model learn the neutral class
        # instead of collapsing to it under class imbalance.
        params["loss_function"] = "MultiClass"
        params["eval_metric"] = "TotalF1"

        params = apply_hardware_params(params)
        # Use only numeric columns for training; drop categorical/text columns
        X_num = X.select_dtypes(include=[np.number]).copy()
        if X_num.empty:
            raise RuntimeError("No numeric features available to train DirectionModel")
        self.feature_names = list(X_num.columns)

        self.model = CatBoostClassifier(**params)

        # Inverse-frequency sample weights to reduce class collapse.
        y_arr = y_labels.to_numpy(dtype=int)
        counts = np.bincount(y_arr, minlength=3).astype(float)
        if counts.sum() <= 0 or (counts > 0).sum() <= 1:
            sample_weight = None
        else:
            max_count = float(counts[counts > 0].max())
            inv = np.zeros_like(counts, dtype=float)
            for i in range(3):
                if counts[i] > 0:
                    inv[i] = max_count / counts[i]
            sample_weight = inv[y_arr]
            # normalize to keep scale reasonable
            sw_mean = float(sample_weight.mean())
            if sw_mean > 0:
                sample_weight = sample_weight / sw_mean

            # Also supply class_weights to CatBoost to help with multiclass imbalance.
            # Normalize class weights to mean ~1.0 to avoid scaling issues inside the estimator.
            class_weights = inv.copy()
            cw_mean = float(class_weights[class_weights > 0].mean()) if (class_weights > 0).any() else 1.0
            if cw_mean > 0:
                class_weights = [float(w / cw_mean) if w > 0 else 0.0 for w in class_weights]
            else:
                class_weights = [1.0, 1.0, 1.0]
            # attach to params used for model construction
            params["class_weights"] = class_weights

        self.model.fit(X_num, y_labels, verbose=False, sample_weight=sample_weight)
        # Training diagnostics: distribution of true vs mapped labels and simple train performance
        try:
            ensure_dirs([MODEL_DIR])
            preds = self.model.predict(X_num)
            preds = np.asarray(preds, dtype=int)
            # CatBoost may return labels with extra dimensions (e.g. (n, 1)).
            preds = preds.reshape(-1)
            pred_signs = np.array([self._rev_map.get(int(p), 0) for p in preds.tolist()], dtype=int)

            pr, rc, f1, sup = precision_recall_fscore_support(true_signs, pred_signs, labels=[-1, 0, 1], zero_division=0)
            cm = confusion_matrix(true_signs, pred_signs, labels=[-1, 0, 1]).tolist()

            vals, cnts = np.unique(preds, return_counts=True)
            predicted_counts = {str(int(v)): int(c) for v, c in zip(vals.tolist(), cnts.tolist())}

            # detect degenerate classifier (predicts effectively one class only)
            self.is_degenerate = len(vals) <= 1

            diag = {
                "train_rows": int(len(y_labels)),
                "true_sign_counts": {"-1": int((true_signs == -1).sum()), "0": int((true_signs == 0).sum()), "1": int((true_signs == 1).sum())},
                "mapped_label_counts": {str(int(k)): int(v) for k, v in y_labels.value_counts().to_dict().items()},
                "predicted_mapped_counts": predicted_counts,
                "per_class": {
                    "-1": {"precision": float(pr[0]), "recall": float(rc[0]), "f1": float(f1[0]), "support": int(sup[0])},
                    "0": {"precision": float(pr[1]), "recall": float(rc[1]), "f1": float(f1[1]), "support": int(sup[1])},
                    "1": {"precision": float(pr[2]), "recall": float(rc[2]), "f1": float(f1[2]), "support": int(sup[2])},
                },
                "confusion_matrix": cm,
                "model_classes": list(getattr(self.model, "classes_", [])),
            }
            # Save diagnostics
            save_json(diag, os.path.join(MODEL_DIR, "direction_training_diagnostics.json"))
            logger.info(f"Saved Direction training diagnostics to {os.path.join(MODEL_DIR, 'direction_training_diagnostics.json')}")
        except Exception:
            logger.exception("Failed to save direction training diagnostics")
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

    def predict_components(self, X: pd.DataFrame) -> Dict[str, np.ndarray]:
        probs = self.predict_proba(X)
        if hasattr(self.model, "classes_"):
            class_labels = np.asarray([int(cl) for cl in list(self.model.classes_)], dtype=int)
        else:
            class_labels = np.arange(probs.shape[1], dtype=int)
        class_signs = np.asarray([self._rev_map.get(int(cl), 0) for cl in class_labels], dtype=float)
        label = class_signs[np.argmax(probs, axis=1)].astype(int)
        expectation = probs.dot(class_signs)
        return {
            "probs": probs,
            "class_labels": class_labels,
            "class_signs": class_signs,
            "label": label,
            "expectation": expectation,
        }

    def predict_sign_expectation(self, X: pd.DataFrame) -> np.ndarray:
        return self.predict_components(X)["expectation"]

    def predict_label(self, X: pd.DataFrame) -> np.ndarray:
        return self.predict_components(X)["label"]

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
        self.label_map = self._normalize_label_map(meta.get("label_map", self.label_map))
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
