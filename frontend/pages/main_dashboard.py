from __future__ import annotations

import pandas as pd
import streamlit as st

from frontend.pages.comparison_page import render_comparison_page
from frontend.pages.diagnostics_page import render_diagnostics_page
from frontend.pages.model_detail_page import render_model_detail_page
from frontend.pages.models_page import render_models_page
from frontend.pages.overview_page import render_overview_page
from frontend.services.loaders import (
    compute_market_summary,
    get_frontend_paths,
    get_latest_prediction,
    list_market_files,
    list_model_keys,
    load_anomaly_windows,
    load_backtest_results,
    load_backtest_summary,
    load_feature_importance,
    load_live_snapshot,
    load_market_data,
    load_model_record,
    load_model_registry,
    load_pipeline_summary,
)


def _format_model_option(model_key: str) -> str:
    return "Main Pipeline" if model_key == "main_direct_pipeline" else str(model_key)


def render_dashboard() -> None:
    st.set_page_config(page_title="Model Analysis Dashboard", layout="wide")
    st.title("Model Analysis Dashboard")

    registry_df = load_model_registry()
    model_keys = registry_df["model_key"].tolist() if not registry_df.empty and "model_key" in registry_df.columns else list_model_keys()
    if not model_keys:
        model_keys = ["main_direct_pipeline"]

    if st.session_state.get("dashboard_selected_model") not in model_keys:
        st.session_state["dashboard_selected_model"] = model_keys[0]

    page = st.sidebar.radio(
        "Navigation",
        ["Overview", "Models", "Model Detail", "Comparison", "Diagnostics"],
        key="dashboard_page",
    )
    st.sidebar.selectbox(
        "Focus model",
        options=model_keys,
        key="dashboard_selected_model",
        format_func=_format_model_option,
    )

    st.sidebar.button("Refresh data", on_click=st.cache_data.clear)

    available_files = list_market_files()
    if available_files:
        default_market = available_files[-1]
        if st.session_state.get("dashboard_market_file") not in available_files:
            st.session_state["dashboard_market_file"] = default_market
        st.sidebar.selectbox(
            "Market file",
            options=available_files,
            key="dashboard_market_file",
        )
    else:
        st.session_state["dashboard_market_file"] = None
        st.sidebar.caption("No market dataset cached.")

    rows_to_show = st.sidebar.slider("Rows to show", min_value=200, max_value=5000, value=1000, step=100)
    show_anomalies = st.sidebar.toggle("Show anomalies", value=True)
    show_range = st.sidebar.toggle("Show range", value=True)

    with st.sidebar.expander("Artifact Paths"):
        st.json(get_frontend_paths())

    selected_model_key = st.session_state.get("dashboard_selected_model", model_keys[0])
    selected_model_record = load_model_record(selected_model_key)
    main_record = load_model_record("main_direct_pipeline")

    market_file = st.session_state.get("dashboard_market_file")
    market_df = load_market_data(market_file) if market_file else pd.DataFrame()
    anomaly_windows_df = load_anomaly_windows()
    backtest_df = load_backtest_results()
    backtest_summary = load_backtest_summary()
    live_snapshot = load_live_snapshot()
    pipeline_summary = load_pipeline_summary()
    feature_importance = load_feature_importance()
    latest_prediction = live_snapshot or get_latest_prediction(backtest_df)
    market_summary = compute_market_summary(market_df) if not market_df.empty else {}

    if page == "Overview":
        render_overview_page(registry_df=registry_df, main_record=main_record)
    elif page == "Models":
        render_models_page(
            registry_df=registry_df,
            selected_model_key=selected_model_key,
            selected_model_record=selected_model_record,
        )
    elif page == "Model Detail":
        render_model_detail_page(selected_model_record)
    elif page == "Comparison":
        render_comparison_page(registry_df=registry_df)
    else:
        render_diagnostics_page(
            market_df=market_df,
            anomaly_windows_df=anomaly_windows_df,
            backtest_df=backtest_df,
            backtest_summary=backtest_summary,
            live_snapshot=live_snapshot,
            pipeline_summary=pipeline_summary,
            feature_importance=feature_importance,
            latest_prediction=latest_prediction,
            market_summary=market_summary,
            rows_to_show=rows_to_show,
            show_anomalies=show_anomalies,
            show_range=show_range,
        )