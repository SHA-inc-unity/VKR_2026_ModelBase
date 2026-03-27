
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pandas as pd

from catboost_floader.monitoring.anomaly_cleaning import annotate_anomalies
from catboost_floader.monitoring.anomaly_online import detect_online_anomaly
from catboost_floader.models.confidence import ErrorCalibrator, compute_confidence, compute_ood_score
from catboost_floader.core.config import ARTIFACTS_DIR, BASE_TIMEFRAME, LIVE_LOOKBACK_DAYS, LOG_DIR, MODEL_DIR, SYMBOL
from catboost_floader.data.ingestion import assemble_market_dataset, fetch_live_microstructure
from catboost_floader.data.preprocessing import preprocess_data
from catboost_floader.features.engineering import build_direct_features, build_range_features
from catboost_floader.models.direct import DirectModel
from catboost_floader.models.range import RangeModel
from catboost_floader.core.utils import ensure_dirs, get_logger, load_json, save_json

logger = get_logger("live_test")


def _safe_align_features(X: pd.DataFrame, feature_names: list[str]) -> pd.DataFrame:
    X2 = X.copy()
    for col in list(X2.columns):
        dt = str(X2[col].dtype)
        if dt == "object" or dt.startswith("string") or "datetime" in dt:
            X2 = X2.drop(columns=[col], errors="ignore")
    if feature_names:
        X2 = X2.reindex(columns=feature_names, fill_value=0.0)
    return X2


def _load_cached_dataset(symbol: str = SYMBOL) -> pd.DataFrame:
    path = os.path.join(ARTIFACTS_DIR, f"{symbol}_{BASE_TIMEFRAME}_market_dataset.csv")
    if not os.path.exists(path):
        raise RuntimeError(f"No cached dataset found at {path}")
    df = pd.read_csv(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return df


def load_models() -> tuple[RangeModel, DirectModel, ErrorCalibrator | None, dict]:
    range_model = RangeModel().load(os.path.join(MODEL_DIR, "range_model"))
    direct_model = DirectModel().load(os.path.join(MODEL_DIR, "direct_model"))
    calibrator_path = os.path.join(MODEL_DIR, "error_calibrator.pkl")
    calibrator = ErrorCalibrator.load(calibrator_path) if os.path.exists(calibrator_path) else None
    meta = load_json(os.path.join(MODEL_DIR, "feature_stats.json")) or {}
    return range_model, direct_model, calibrator, meta


def run_live_test(
    range_model: Optional[RangeModel] = None,
    direct_model: Optional[DirectModel] = None,
    calibrator: Optional[ErrorCalibrator] = None,
    feature_stats: Optional[dict] = None,
    symbol: str = SYMBOL,
) -> Dict[str, Any]:
    if range_model is None or direct_model is None:
        range_model, direct_model, calibrator_loaded, stats_loaded = load_models()
        calibrator = calibrator or calibrator_loaded
        feature_stats = feature_stats or stats_loaded

    try:
        df = assemble_market_dataset(symbol=symbol, lookback_days=LIVE_LOOKBACK_DAYS)
    except Exception as exc:
        logger.warning(f"Live dataset refresh failed, falling back to cached dataset: {exc}")
        df = _load_cached_dataset(symbol=symbol)

    try:
        micro = fetch_live_microstructure(symbol=symbol)
    except Exception as exc:
        logger.warning(f"Live microstructure refresh failed, continuing without it: {exc}")
        micro = {}

    if micro:
        for key, value in micro.items():
            if key != "timestamp":
                df[key] = value

    df = preprocess_data(df)
    direct_features = annotate_anomalies(build_direct_features(df))
    range_features = annotate_anomalies(build_range_features(df))

    direct_row = direct_features.iloc[-1].copy()
    range_row = range_features.iloc[-1].copy()

    x_direct = _safe_align_features(
        direct_features.iloc[[-1]].drop(columns=["timestamp", "anomaly_type", "anomaly_level", "regime"], errors="ignore"),
        getattr(direct_model, "feature_names", []),
    )
    x_range = _safe_align_features(
        range_features.iloc[[-1]].drop(columns=["timestamp", "anomaly_type", "anomaly_level", "regime"], errors="ignore"),
        getattr(range_model, "feature_names", []),
    )

    pred_return = float(direct_model.predict(x_direct)[0])
    current_close = float(direct_row["close"])
    pred_price = current_close * (1.0 + pred_return)

    anomaly = detect_online_anomaly(direct_row)

    range_pred = range_model.predict(
        x_range,
        current_close=pd.Series([current_close]),
        direct_pred_return=pd.Series([pred_return]),
        anomaly_flag=pd.Series([anomaly["anomaly_flag"]]),
    )[0]

    feature_stats = feature_stats or {}
    ood_score = compute_ood_score(direct_row, feature_stats)
    band_width_norm = float((range_pred[1] - range_pred[0]) / (abs(current_close) + 1e-8))

    if calibrator is not None:
        x_cal = _safe_align_features(
            direct_features.iloc[[-1]].drop(columns=["timestamp", "anomaly_type", "anomaly_level", "regime"], errors="ignore"),
            getattr(calibrator, "feature_names", []),
        )
        predicted_abs_error = float(calibrator.predict(x_cal)[0])
    else:
        predicted_abs_error = band_width_norm

    confidence = compute_confidence(predicted_abs_error, anomaly["anomaly_score"], ood_score, band_width_norm)

    result = {
        "timestamp": str(direct_row.get("timestamp", range_row.get("timestamp", ""))),
        "symbol": symbol,
        "current_close": current_close,
        "direct_pred_return": pred_return,
        "direct_pred_price": pred_price,
        "range_pred_low": float(range_pred[0]),
        "range_pred_high": float(range_pred[1]),
        "predicted_abs_error": predicted_abs_error,
        "confidence": float(confidence),
        "anomaly_flag": anomaly["anomaly_flag"],
        "anomaly_type": anomaly["anomaly_type"],
        "anomaly_score": float(anomaly["anomaly_score"]),
        "ood_score": float(ood_score),
        "band_width_norm": band_width_norm,
        "explanation": anomaly["explanation"],
        "data_source": "live_refresh" if micro else "cached_dataset",
    }

    ensure_dirs([LOG_DIR, ARTIFACTS_DIR])
    save_json(result, os.path.join(LOG_DIR, "latest_live_prediction.json"))

    hist_path = os.path.join(LOG_DIR, "live_predictions_history.csv")
    hist_row = pd.DataFrame([result])
    if os.path.exists(hist_path):
        prev = pd.read_csv(hist_path)
        hist_row = pd.concat([prev, hist_row], ignore_index=True)
    hist_row.to_csv(hist_path, index=False)
    return result
