from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def detect_online_anomaly(row: pd.Series) -> Dict[str, Any]:
    score = float(row.get("anomaly_score", 0.0))
    flag = bool(int(row.get("anomaly_flag", 0)) == 1 or score >= 1.0)
    anomaly_type = row.get("anomaly_type", "normal")
    explanation = "No anomaly" if not flag else f"Detected {anomaly_type} with score {score:.2f}"
    return {
        "anomaly_flag": flag,
        "anomaly_score": float(np.clip(score, 0, 1)),
        "anomaly_type": anomaly_type,
        "explanation": explanation,
    }
