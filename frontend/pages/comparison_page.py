from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from frontend.pages.models_page import filter_registry


def render_comparison_page(*, registry_df: pd.DataFrame) -> None:
    st.header("Comparison")

    if registry_df is None or registry_df.empty:
        st.info("No model registry artifacts were found.")
        return

    c1, c2, c3, c4 = st.columns(4)
    only_eligible = c1.checkbox("Only eligible", value=True)
    only_robust = c2.checkbox("Only robust", value=False)
    only_overfit_risk = c3.checkbox("Only overfit-risk", value=False)
    only_positive_delta = c4.checkbox("Only positive delta", value=False)

    filtered = filter_registry(
        registry_df,
        only_eligible=only_eligible,
        only_robust=only_robust,
        only_overfit_risk=only_overfit_risk,
        only_positive_delta=only_positive_delta,
    )
    if filtered.empty:
        st.info("No models match the current comparison filters.")
        return

    default_models = filtered["model_name"].head(min(4, len(filtered))).tolist()
    selected_names = st.multiselect(
        "Models to compare",
        options=filtered["model_name"].tolist(),
        default=default_models,
    )
    if not selected_names:
        st.info("Select at least one model to compare.")
        return

    selected_df = filtered[filtered["model_name"].isin(selected_names)].copy()
    selected_df = selected_df.set_index("model_name")

    comparison_fields = {
        "delta_vs_baseline": "Delta vs Baseline",
        "raw_model_delta_vs_baseline": "Raw Delta vs Baseline",
        "mean_delta_vs_baseline": "Mean Delta vs Baseline",
        "std_delta_vs_baseline": "Std Delta vs Baseline",
        "win_rate_vs_baseline": "Win Rate vs Baseline",
        "sign_acc_pct": "Sign Accuracy %",
        "direction_acc_pct": "Direction Accuracy %",
        "overfit_status": "Overfit Status",
        "robustness_status": "Robustness Status",
        "selection_eligibility": "Selection Eligibility",
        "recommendation_bucket": "Recommendation Bucket",
    }
    default_fields = [
        "delta_vs_baseline",
        "mean_delta_vs_baseline",
        "sign_acc_pct",
        "direction_acc_pct",
        "overfit_status",
        "robustness_status",
        "selection_eligibility",
    ]
    field_selection = st.multiselect(
        "Fields",
        options=list(comparison_fields.keys()),
        default=default_fields,
        format_func=lambda key: comparison_fields[key],
    )
    if not field_selection:
        st.info("Choose at least one field to compare.")
        return

    table = selected_df[field_selection].rename(columns=comparison_fields)
    st.dataframe(table, use_container_width=True)

    numeric_metric = st.selectbox(
        "Chart metric",
        options=[
            "delta_vs_baseline",
            "raw_model_delta_vs_baseline",
            "mean_delta_vs_baseline",
            "sign_acc_pct",
            "direction_acc_pct",
            "win_rate_vs_baseline",
        ],
        format_func=lambda key: comparison_fields[key],
    )
    chart_df = selected_df.reset_index()[["model_name", numeric_metric]].copy()
    chart_df[numeric_metric] = pd.to_numeric(chart_df[numeric_metric], errors="coerce")
    chart_df = chart_df.dropna(subset=[numeric_metric])
    if not chart_df.empty:
        fig = go.Figure(
            go.Bar(
                x=chart_df["model_name"],
                y=chart_df[numeric_metric],
                text=chart_df[numeric_metric].round(2),
                textposition="auto",
            )
        )
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), yaxis_title=comparison_fields[numeric_metric])
        st.plotly_chart(fig, use_container_width=True)

    st.caption("Comparison remains read-only. No model execution or config changes are exposed here.")