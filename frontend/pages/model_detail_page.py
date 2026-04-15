from __future__ import annotations

import streamlit as st

from frontend.components.model_analysis import (
    render_model_artifacts_section,
    render_model_overfitting_section,
    render_model_robustness_section,
    render_model_selection_section,
    render_model_summary_section,
)
from frontend.services.formatters import fmt_bool, fmt_text


def render_model_detail_page(record: dict | None) -> None:
    st.header("Model Detail")

    if not record:
        st.info("Choose a model from the sidebar to inspect its diagnostics.")
        return

    summary = dict(record.get("summary", {}) or {})
    registry = dict(record.get("registry", {}) or {})

    st.subheader(record.get("model_name", record.get("model_key", "Model")))
    st.caption(
        " | ".join(
            [
                f"Robustness: {fmt_text(summary.get('robustness_status'))}",
                f"Eligible: {fmt_bool(summary.get('selection_eligibility'))}",
                f"Overfit: {fmt_text(summary.get('overfit_status'))}",
                f"Recommendation: {fmt_text(registry.get('recommendation_bucket'))}",
            ]
        )
    )

    tab_summary, tab_overfit, tab_robustness, tab_selection, tab_artifacts = st.tabs(
        ["Summary", "Overfitting", "Robustness", "Selection / Strategy", "Artifacts"]
    )

    with tab_summary:
        render_model_summary_section(record)
    with tab_overfit:
        render_model_overfitting_section(record)
    with tab_robustness:
        render_model_robustness_section(record)
    with tab_selection:
        render_model_selection_section(record)
    with tab_artifacts:
        render_model_artifacts_section(record)