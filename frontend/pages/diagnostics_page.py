from __future__ import annotations

import pandas as pd
import streamlit as st

from frontend.components.alerts import render_alerts
from frontend.components.charts import (
    render_backtest_chart,
    render_confidence_chart,
    render_confidence_gauge,
    render_coverage_chart,
    render_error_histogram,
    render_feature_importance,
    render_price_chart,
)
from frontend.components.metrics import render_market_metrics, render_prediction_metrics
from frontend.components.tables import render_anomalies_table, render_backtest_table, render_market_table


def render_diagnostics_page(
    *,
    market_df: pd.DataFrame,
    anomaly_windows_df: pd.DataFrame,
    backtest_df: pd.DataFrame,
    backtest_summary: dict | None,
    live_snapshot: dict | None,
    pipeline_summary: dict | None,
    feature_importance: dict | None,
    latest_prediction: dict | None,
    market_summary: dict,
    rows_to_show: int,
    show_anomalies: bool,
    show_range: bool,
) -> None:
    st.header("Diagnostics")

    if market_df is None or market_df.empty:
        st.info("No cached market data found.")
        return

    render_alerts(anomaly_windows_df, latest_prediction)
    render_market_metrics(market_summary)
    render_prediction_metrics(latest_prediction, backtest_summary)

    tab_market, tab_prediction, tab_backtest, tab_artifacts = st.tabs(["Market", "Predictions", "Backtest", "Artifacts"])

    with tab_market:
        st.subheader("Price & Forecast")
        render_price_chart(
            market_df,
            anomaly_windows_df if show_anomalies else None,
            rows_to_show,
            latest_prediction if show_range else None,
        )
        c1, c2 = st.columns([2, 1])
        with c1:
            st.subheader("Recent Market Data")
            render_market_table(market_df)
        with c2:
            st.subheader("Anomaly Windows")
            render_anomalies_table(anomaly_windows_df)

    with tab_prediction:
        c1, c2 = st.columns([2, 1])
        with c1:
            render_confidence_chart(backtest_df, min(rows_to_show, 1000))
        with c2:
            render_confidence_gauge((latest_prediction or {}).get("confidence") if latest_prediction else None)
        if latest_prediction:
            st.subheader("Latest Prediction Snapshot")
            st.json(latest_prediction)
        if live_snapshot:
            with st.expander("Live Snapshot JSON"):
                st.json(live_snapshot)

    with tab_backtest:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Forecast vs Actual")
            render_backtest_chart(backtest_df, min(rows_to_show, 1000))
        with c2:
            st.subheader("Error Distribution")
            render_error_histogram(backtest_df)
        st.subheader("Coverage Timeline")
        render_coverage_chart(backtest_df, min(rows_to_show, 1000))
        st.subheader("Backtest Rows")
        render_backtest_table(backtest_df)

    with tab_artifacts:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Direct Feature Importance")
            render_feature_importance(feature_importance, "direct")
        with c2:
            st.subheader("Range Feature Importance")
            render_feature_importance(feature_importance, "range_low")

        if pipeline_summary:
            with st.expander("Pipeline Summary JSON"):
                st.json(pipeline_summary)
        if backtest_summary:
            with st.expander("Backtest Summary JSON"):
                st.json(backtest_summary)