import os
import sys

import streamlit as st

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)

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
from frontend.services.loaders import (
    compute_market_summary,
    get_latest_prediction,
    list_market_files,
    load_anomalies,
    load_anomaly_windows,
    load_backtest_results,
    load_backtest_summary,
    load_feature_importance,
    load_live_snapshot,
    load_market_data,
    load_pipeline_summary,
)

st.set_page_config(page_title="Crypto Forecast Dashboard", layout="wide")
st.title("Crypto Forecast Dashboard")

available_files = list_market_files()
selected_file = st.sidebar.selectbox("Market file", available_files, index=len(available_files) - 1 if available_files else None)
rows_to_show = st.sidebar.slider("Rows to show", min_value=200, max_value=5000, value=1000, step=100)
show_anomalies = st.sidebar.toggle("Show anomalies", value=True)
show_range = st.sidebar.toggle("Show range", value=True)
st.sidebar.button("Refresh data", on_click=st.cache_data.clear)

market_df = load_market_data(selected_file) if selected_file else None
anomalies_df = load_anomalies()
anomaly_windows_df = load_anomaly_windows()
backtest_df = load_backtest_results()
backtest_summary = load_backtest_summary()
live_snapshot = load_live_snapshot()
pipeline_summary = load_pipeline_summary()
feature_importance = load_feature_importance()
latest_prediction = live_snapshot or get_latest_prediction(backtest_df)
market_summary = compute_market_summary(market_df) if market_df is not None and not market_df.empty else {}

if market_df is None or market_df.empty:
    st.error("No cached market data found. Run the backend pipeline first.")
    st.stop()

render_alerts(anomaly_windows_df, latest_prediction)
render_market_metrics(market_summary)
render_prediction_metrics(latest_prediction, backtest_summary)

tab_market, tab_prediction, tab_backtest, tab_diagnostics = st.tabs(["Market", "Predictions", "Backtest", "Diagnostics"])

with tab_market:
    st.subheader("Price & Forecast")
    render_price_chart(market_df, anomaly_windows_df if show_anomalies else None, rows_to_show, latest_prediction if show_range else None)
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
        st.json(latest_prediction)

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

with tab_diagnostics:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Direct Feature Importance")
        render_feature_importance(feature_importance, "direct")
    with c2:
        st.subheader("Range Feature Importance")
        render_feature_importance(feature_importance, "range_low")
    if pipeline_summary:
        st.subheader("Pipeline Summary")
        st.json(pipeline_summary)
