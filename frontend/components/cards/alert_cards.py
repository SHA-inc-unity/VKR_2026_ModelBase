from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from frontend.services.formatters import fmt_confidence


def render_alerts(anomaly_windows_df: pd.DataFrame, latest_prediction: Optional[dict]) -> None:
    anomaly_count = 0 if anomaly_windows_df is None or anomaly_windows_df.empty else len(anomaly_windows_df)
    severe_count = 0 if anomaly_windows_df is None or anomaly_windows_df.empty else int((anomaly_windows_df.get("severity", "") == "severe").sum())
    if anomaly_count > 0:
        st.error(f"Historical anomaly windows: {anomaly_count}. Severe windows: {severe_count}.")
    else:
        st.success("No anomaly windows found in the current log.")

    if not latest_prediction:
        st.info("No prediction snapshot found yet. Run the pipeline to populate forecast cards.")
        return

    anomaly_flag = bool(latest_prediction.get("anomaly_flag", False))
    confidence = latest_prediction.get("confidence")
    anomaly_type = latest_prediction.get("anomaly_type", "normal")
    anomaly_level = latest_prediction.get("anomaly_level", anomaly_type)

    if anomaly_flag:
        st.error(f"Live anomaly detected: {anomaly_level} / {anomaly_type}.")
    elif confidence is not None and float(confidence) < 0.4:
        st.warning(f"Low confidence forecast: {fmt_confidence(confidence)}")
    else:
        st.success("Latest prediction window looks stable.")
