from __future__ import annotations

import pandas as pd
import streamlit as st

from frontend.components.model_analysis import render_model_registry_table, render_model_summary_section


def filter_registry(
    registry_df: pd.DataFrame,
    *,
    only_eligible: bool = False,
    only_robust: bool = False,
    only_overfit_risk: bool = False,
    only_positive_delta: bool = False,
    search_text: str = "",
) -> pd.DataFrame:
    if registry_df is None or registry_df.empty:
        return pd.DataFrame()

    filtered = registry_df.copy()
    if only_eligible and "selection_eligibility" in filtered.columns:
        filtered = filtered[filtered["selection_eligibility"].fillna(False).astype(bool)]
    if only_robust and "robustness_status" in filtered.columns:
        filtered = filtered[filtered["robustness_status"].fillna("").astype(str).str.startswith("robust")]
    if only_overfit_risk and "overfit_status" in filtered.columns:
        filtered = filtered[filtered["overfit_status"].fillna("").isin(["moderate", "severe"])]
    if only_positive_delta and "delta_vs_baseline" in filtered.columns:
        filtered = filtered[pd.to_numeric(filtered["delta_vs_baseline"], errors="coerce") > 0]
    if search_text:
        needle = search_text.strip().lower()
        if needle:
            filtered = filtered[
                filtered["model_name"].fillna("").astype(str).str.lower().str.contains(needle)
                | filtered["model_key"].fillna("").astype(str).str.lower().str.contains(needle)
            ]
    return filtered.reset_index(drop=True)


def render_models_page(
    *,
    registry_df: pd.DataFrame,
    selected_model_key: str | None,
    selected_model_record: dict | None,
) -> None:
    st.header("Models")

    if registry_df is None or registry_df.empty:
        st.info("No model registry artifacts were found.")
        return

    c1, c2, c3, c4 = st.columns(4)
    only_eligible = c1.checkbox("Only eligible", value=False)
    only_robust = c2.checkbox("Only robust", value=False)
    only_overfit_risk = c3.checkbox("Only overfit-risk", value=False)
    only_positive_delta = c4.checkbox("Only positive delta", value=False)
    search_text = st.text_input("Search models", value="", placeholder="Filter by model name or key")

    filtered = filter_registry(
        registry_df,
        only_eligible=only_eligible,
        only_robust=only_robust,
        only_overfit_risk=only_overfit_risk,
        only_positive_delta=only_positive_delta,
        search_text=search_text,
    )

    st.caption(f"Showing {len(filtered)} of {len(registry_df)} models")
    render_model_registry_table(filtered)

    if selected_model_key and selected_model_record:
        st.subheader("Focused Model")
        st.caption(f"Sidebar selection: {selected_model_record.get('model_name', selected_model_key)}")
        render_model_summary_section(selected_model_record)