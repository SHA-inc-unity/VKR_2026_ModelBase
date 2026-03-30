from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from catboost_floader.core.config import MODEL_DIR, RANGE_HIGH_CATBOOST_PARAMS, RANGE_LOW_CATBOOST_PARAMS, RANDOM_SEED, apply_hardware_params
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json

logger = get_logger("model_range")


class RangeModel:
    def __init__(
        self,
        low_params: Optional[Dict[str, Any]] = None,
        high_params: Optional[Dict[str, Any]] = None,
        calibration: Optional[Dict[str, Any]] = None,
    ):
        self.low_model: Optional[CatBoostRegressor] = None
        self.high_model: Optional[CatBoostRegressor] = None
        self.low_params = low_params or RANGE_LOW_CATBOOST_PARAMS.copy()
        self.high_params = high_params or RANGE_HIGH_CATBOOST_PARAMS.copy()
        self.feature_names: list[str] = []
        self.calibration: Dict[str, Any] = calibration or {
            "scale_normal": 1.0,
            "scale_anomaly": 1.0,
            "margin_normal": 0.0,
            "margin_anomaly": 0.0,
            "center_mode": "model_center",
        }

    def fit(self, X: pd.DataFrame, y_low: pd.Series, y_high: pd.Series):
        low_params = apply_hardware_params({**self.low_params, "random_seed": RANDOM_SEED})
        high_params = apply_hardware_params({**self.high_params, "random_seed": RANDOM_SEED})
        self.feature_names = list(X.columns)
        self.low_model = CatBoostRegressor(**low_params)
        self.high_model = CatBoostRegressor(**high_params)
        self.low_model.fit(X, y_low, verbose=False)
        self.high_model.fit(X, y_high, verbose=False)
        logger.info("Trained RangeModel")
        return self

    def _prepare_inference_frame(self, X: pd.DataFrame) -> pd.DataFrame:
        X2 = X.copy()
        for col in list(X2.columns):
            dt = str(X2[col].dtype)
            if dt == "object" or dt.startswith("string") or "datetime" in dt:
                X2 = X2.drop(columns=[col], errors="ignore")
        return X2

    def _apply_calibration(self, X_original: pd.DataFrame, low_raw: np.ndarray, high_raw: np.ndarray) -> np.ndarray:
        low_raw = np.asarray(low_raw, dtype=float)
        high_raw = np.asarray(high_raw, dtype=float)
        center_mode = str(self.calibration.get("center_mode", "model_center"))
        # NOTE: center_mode can be "direct_center" (calibrated around direct model center)
        # but direct-center requires direct-model outputs to be supplied at inference time.
        # If they are missing, we fall back to model-center to keep the model usable.
        center = (low_raw + high_raw) / 2.0 if center_mode == "model_center" else (low_raw + high_raw) / 2.0
        half = np.maximum((high_raw - low_raw) / 2.0, 0.0)

        anomaly_flags = None
        if "anomaly_flag" in X_original.columns:
            anomaly_flags = pd.to_numeric(X_original["anomaly_flag"], errors="coerce").fillna(0).to_numpy(dtype=int)
        else:
            anomaly_flags = np.zeros(len(center), dtype=int)

        scale_normal = float(self.calibration.get("scale_normal", 1.0))
        scale_anomaly = float(self.calibration.get("scale_anomaly", 1.0))
        margin_normal = float(self.calibration.get("margin_normal", 0.0))
        margin_anomaly = float(self.calibration.get("margin_anomaly", 0.0))
        scale = np.where(anomaly_flags == 1, scale_anomaly, scale_normal)
        margin = np.where(anomaly_flags == 1, margin_anomaly, margin_normal)
        hw = half * scale + margin
        low_adj = center - hw
        high_adj = center + hw
        low = np.minimum(low_adj, high_adj)
        high = np.maximum(low_adj, high_adj)
        return np.vstack([low, high]).T

    def _apply_calibration_with_optional_direct_center(
        self,
        X_original: pd.DataFrame,
        low_raw: np.ndarray,
        high_raw: np.ndarray,
        current_close: Optional[pd.Series | np.ndarray] = None,
        direct_pred_return: Optional[pd.Series | np.ndarray] = None,
        anomaly_flag: Optional[pd.Series | np.ndarray] = None,
    ) -> np.ndarray:
        low_raw = np.asarray(low_raw, dtype=float)
        high_raw = np.asarray(high_raw, dtype=float)
        center_mode = str(self.calibration.get("center_mode", "model_center"))

        model_center = (low_raw + high_raw) / 2.0
        center = model_center
        if center_mode == "direct_center":
            if current_close is not None and direct_pred_return is not None:
                cur = np.asarray(current_close, dtype=float)
                dpr = np.asarray(direct_pred_return, dtype=float)
                if cur.shape[0] == center.shape[0] and dpr.shape[0] == center.shape[0]:
                    center = cur * (1.0 + dpr)

        half = np.maximum((high_raw - low_raw) / 2.0, 0.0)

        if anomaly_flag is not None:
            anomaly_flags = np.asarray(anomaly_flag, dtype=float)
            anomaly_flags = np.where(np.isnan(anomaly_flags), 0, anomaly_flags).astype(int)
        elif "anomaly_flag" in X_original.columns:
            anomaly_flags = pd.to_numeric(X_original["anomaly_flag"], errors="coerce").fillna(0).to_numpy(dtype=int)
        else:
            anomaly_flags = np.zeros(len(center), dtype=int)

        scale_normal = float(self.calibration.get("scale_normal", 1.0))
        scale_anomaly = float(self.calibration.get("scale_anomaly", 1.0))
        margin_normal = float(self.calibration.get("margin_normal", 0.0))
        margin_anomaly = float(self.calibration.get("margin_anomaly", 0.0))
        scale = np.where(anomaly_flags == 1, scale_anomaly, scale_normal)
        margin = np.where(anomaly_flags == 1, margin_anomaly, margin_normal)
        hw = half * scale + margin
        low_adj = center - hw
        high_adj = center + hw

        low = np.minimum(low_adj, high_adj)
        high = np.maximum(low_adj, high_adj)
        return np.vstack([low, high]).T

    def predict(
        self,
        X: pd.DataFrame,
        *,
        current_close: Optional[pd.Series | np.ndarray] = None,
        direct_pred_return: Optional[pd.Series | np.ndarray] = None,
        anomaly_flag: Optional[pd.Series | np.ndarray] = None,
    ) -> np.ndarray:
        if self.low_model is None or self.high_model is None:
            raise RuntimeError("RangeModel is not trained or loaded")
        X_orig = self._prepare_inference_frame(X)
        X_use = X_orig.reindex(columns=self.feature_names, fill_value=0.0) if self.feature_names else X_orig
        low_raw = self.low_model.predict(X_use)
        high_raw = self.high_model.predict(X_use)
        return self._apply_calibration_with_optional_direct_center(
            X_original=X_orig,
            low_raw=low_raw,
            high_raw=high_raw,
            current_close=current_close,
            direct_pred_return=direct_pred_return,
            anomaly_flag=anomaly_flag,
        )

    def save(self, prefix: str):
        if self.low_model is None or self.high_model is None:
            raise RuntimeError("RangeModel is not trained")
        ensure_dirs([os.path.dirname(prefix)])
        self.low_model.save_model(prefix + "_low.cbm")
        self.high_model.save_model(prefix + "_high.cbm")
        save_json(
            {
                "feature_names": self.feature_names,
                "low_params": self.low_params,
                "high_params": self.high_params,
                "calibration": self.calibration,
            },
            prefix + ".json",
        )

    def load(self, prefix: str):
        self.low_model = CatBoostRegressor()
        self.high_model = CatBoostRegressor()
        self.low_model.load_model(prefix + "_low.cbm")
        self.high_model.load_model(prefix + "_high.cbm")
        meta = load_json(prefix + ".json") or {}
        self.feature_names = meta.get("feature_names", [])
        self.low_params = meta.get("low_params", self.low_params)
        self.high_params = meta.get("high_params", self.high_params)
        self.calibration = meta.get("calibration", self.calibration)
        return self


def train_range_model(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    low_params: Optional[Dict[str, Any]] = None,
    high_params: Optional[Dict[str, Any]] = None,
    calibration: Optional[Dict[str, Any]] = None,
    save: bool = True,
) -> RangeModel:
    model = RangeModel(low_params=low_params, high_params=high_params, calibration=calibration)
    model.fit(features, targets["target_range_low"], targets["target_range_high"])
    if save:
        ensure_dirs([MODEL_DIR])
        model.save(os.path.join(MODEL_DIR, "range_model"))
    return model
