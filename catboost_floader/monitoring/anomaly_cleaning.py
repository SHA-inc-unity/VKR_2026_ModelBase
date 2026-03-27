from __future__ import annotations

import os
from typing import Tuple

import numpy as np
import pandas as pd

from catboost_floader.core.config import (
    ANOMALY_GRACE_MINUTES,
    ANOMALY_ORDERBOOK_Z,
    ANOMALY_RETURN_Z,
    ANOMALY_SPREAD_Z,
    ANOMALY_VOLUME_Z,
    LOG_DIR,
    SEVERE_ANOMALY_SCORE,
)
from catboost_floader.core.utils import ensure_dirs, get_logger

logger = get_logger("anomaly_cleaning")


def robust_zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    median = s.median()
    mad = np.median(np.abs(s - median)) + 1e-8
    return 0.6745 * (s - median) / mad


def annotate_anomalies(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy().reset_index(drop=True)
    if out.empty:
        for col, default in [
            ("return_anomaly_z", 0.0), ("volume_anomaly_z", 0.0), ("spread_anomaly_z", 0.0),
            ("orderbook_anomaly_z", 0.0), ("regime_anomaly_z", 0.0), ("anomaly_score", 0.0),
            ("anomaly_flag", 0), ("anomaly_type", "normal"), ("anomaly_level", "normal")
        ]:
            out[col] = pd.Series(dtype=type(default))
        return out

    ret_src = out["log_return_1"] if "log_return_1" in out.columns else out.get("return_1", pd.Series(np.zeros(len(out))))
    vol_src = out.get("volume_zscore_12", out.get("volume", pd.Series(np.zeros(len(out)))))
    spread_src = out.get("spread_zscore", out.get("top_spread", out.get("hl_spread", pd.Series(np.zeros(len(out))))))
    ob_src = out.get("top_imbalance", out.get("depth_imbalance", pd.Series(np.zeros(len(out)))))
    regime_src = out.get("vol_ratio_3_36", out.get("vol_ratio_6_36", pd.Series(np.zeros(len(out)))))

    out["return_anomaly_z"] = robust_zscore(ret_src)
    out["volume_anomaly_z"] = robust_zscore(vol_src)
    out["spread_anomaly_z"] = robust_zscore(spread_src)
    out["orderbook_anomaly_z"] = robust_zscore(ob_src)
    out["regime_anomaly_z"] = robust_zscore(regime_src)

    type_labels = []
    scores = []
    flags = []
    levels = []
    for _, row in out.iterrows():
        candidates = {
            "price_shock": abs(float(row.get("return_anomaly_z", 0.0))) / ANOMALY_RETURN_Z,
            "volume_shock": abs(float(row.get("volume_anomaly_z", 0.0))) / ANOMALY_VOLUME_Z,
            "spread_shock": abs(float(row.get("spread_anomaly_z", 0.0))) / ANOMALY_SPREAD_Z,
            "orderbook_stress": abs(float(row.get("orderbook_anomaly_z", 0.0))) / ANOMALY_ORDERBOOK_Z,
            "regime_shift": abs(float(row.get("regime_anomaly_z", 0.0))) / ANOMALY_RETURN_Z,
        }
        anomaly_type = max(candidates, key=candidates.get)
        raw_score = max(candidates.values())
        score = float(np.clip(raw_score, 0.0, 1.5))
        if score >= 1.2:
            level = "shock"
        elif score >= 0.9:
            level = "stress"
        elif score >= 0.6:
            level = "warning"
        else:
            level = "normal"
        flags.append(int(level != "normal"))
        scores.append(score)
        levels.append(level)
        type_labels.append(anomaly_type if level != "normal" else "normal")

    out["anomaly_score"] = scores
    out["anomaly_flag"] = flags
    out["anomaly_type"] = type_labels
    out["anomaly_level"] = levels
    return out


def build_anomaly_windows(features: pd.DataFrame) -> pd.DataFrame:
    cols = ["start_ts", "end_ts", "anomaly_type", "anomaly_level", "severity", "peak_score", "point_count", "duration_min", "price_min", "price_max"]
    if features.empty or "anomaly_flag" not in features.columns:
        return pd.DataFrame(columns=cols)
    flagged = features[features["anomaly_flag"] == 1].copy()
    if flagged.empty:
        return pd.DataFrame(columns=cols)
    flagged = flagged.sort_values("timestamp").reset_index(drop=True)
    windows = []
    start_idx = 0
    for i in range(1, len(flagged)):
        gap = (flagged.loc[i, "timestamp"] - flagged.loc[i - 1, "timestamp"]).total_seconds() / 60.0
        if gap > ANOMALY_GRACE_MINUTES:
            windows.append(flagged.iloc[start_idx:i].copy())
            start_idx = i
    windows.append(flagged.iloc[start_idx:].copy())

    rows = []
    for win in windows:
        peak_idx = win["anomaly_score"].idxmax()
        peak_type = win.loc[peak_idx, "anomaly_type"]
        level = win.loc[peak_idx, "anomaly_level"]
        peak_score = float(win["anomaly_score"].max())
        severity = "severe" if peak_score >= SEVERE_ANOMALY_SCORE else "moderate"
        rows.append({
            "start_ts": win["timestamp"].iloc[0],
            "end_ts": win["timestamp"].iloc[-1],
            "anomaly_type": peak_type,
            "anomaly_level": level,
            "severity": severity,
            "peak_score": peak_score,
            "point_count": int(len(win)),
            "duration_min": float((win["timestamp"].iloc[-1] - win["timestamp"].iloc[0]).total_seconds() / 60.0 + 1),
            "price_min": float(win["close"].min()) if "close" in win.columns else np.nan,
            "price_max": float(win["close"].max()) if "close" in win.columns else np.nan,
        })
    return pd.DataFrame(rows)


def persist_anomaly_artifacts(features: pd.DataFrame) -> None:
    ensure_dirs([LOG_DIR])
    flagged = features[features.get("anomaly_flag", 0) == 1].copy()
    flagged.to_csv(os.path.join(LOG_DIR, "anomaly_flags.csv"), index=False)
    build_anomaly_windows(features).to_csv(os.path.join(LOG_DIR, "anomaly_windows.csv"), index=False)


def clean_training_anomalies(features: pd.DataFrame, targets: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if features.empty or targets.empty:
        return features.reset_index(drop=True), targets.reset_index(drop=True)
    annotated = annotate_anomalies(features)
    mask = annotated["anomaly_level"] != "shock"
    cleaned_features = annotated.loc[mask].reset_index(drop=True)
    cleaned_targets = targets.loc[mask].reset_index(drop=True)
    logger.info(f"Training anomaly cleaning removed {int((~mask).sum())} shock rows")
    return cleaned_features, cleaned_targets


def clean_anomalies(features: pd.DataFrame, targets: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    return clean_training_anomalies(features, targets)
