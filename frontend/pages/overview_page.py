from __future__ import annotations

import pandas as pd
import streamlit as st

from frontend.components.model_analysis import render_model_registry_table
from frontend.services.formatters import fmt_bool, fmt_delta, fmt_number, fmt_percent, fmt_text


def render_overview_page(
    *,
    registry_df: pd.DataFrame,
    main_record: dict | None,
) -> None:
    st.header("Overview")

    if registry_df is None or registry_df.empty:
        st.info("No model registry artifacts were found. Populate outputs first, then refresh the dashboard.")
        return

    eligible_count = int(registry_df["selection_eligibility"].fillna(False).astype(bool).sum()) if "selection_eligibility" in registry_df.columns else 0
    robust_count = int(registry_df["robustness_status"].fillna("").astype(str).str.startswith("robust").sum()) if "robustness_status" in registry_df.columns else 0
    positive_delta_count = int((pd.to_numeric(registry_df.get("delta_vs_baseline"), errors="coerce") > 0).sum()) if "delta_vs_baseline" in registry_df.columns else 0
    overfit_risk_count = int(registry_df["overfit_status"].fillna("").isin(["moderate", "severe"]).sum()) if "overfit_status" in registry_df.columns else 0
    suppressed_edge_count = int(
        (
            (pd.to_numeric(registry_df.get("raw_model_delta_vs_baseline"), errors="coerce") > 0)
            & (
                (pd.to_numeric(registry_df.get("delta_vs_baseline"), errors="coerce") <= 0)
                | (~registry_df.get("selection_eligibility", pd.Series(False, index=registry_df.index)).fillna(False).astype(bool))
            )
        ).sum()
    ) if {"raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility"}.issubset(registry_df.columns) else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Models", str(len(registry_df)))
    c2.metric("Eligible", str(eligible_count))
    c3.metric("Robust", str(robust_count))
    c4.metric("Positive Delta", str(positive_delta_count))
    c5.metric("Overfit Risk", str(overfit_risk_count))

    c6, c7 = st.columns(2)
    c6.metric("Suppressed Edge", str(suppressed_edge_count))
    if main_record:
        summary = dict(main_record.get("summary", {}) or {})
        selection = dict(main_record.get("selection", {}) or {})
        c7.metric(
            "Main Pipeline",
            fmt_text(summary.get("robustness_status")),
            delta=fmt_delta(summary.get("delta_vs_baseline")),
        )

        st.subheader("Main Pipeline Snapshot")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("MAE", fmt_number(summary.get("MAE")))
        m2.metric("Sign %", fmt_percent(summary.get("sign_acc_pct"), scale_100=False))
        m3.metric("Direction %", fmt_percent(summary.get("direction_acc_pct"), scale_100=False))
        m4.metric("Eligible", fmt_bool(summary.get("selection_eligibility")))
        m5.metric("Candidate", fmt_text(selection.get("guarded_candidate_type") or selection.get("selected_candidate_type")))
        m6.metric("Guard Reason", fmt_text(selection.get("final_holdout_guard_reason")))

    st.subheader("Top Guarded Winners")
    winners = registry_df.copy()
    if "delta_vs_baseline" in winners.columns:
        winners = winners.sort_values(by="delta_vs_baseline", ascending=False, na_position="last")
    st.dataframe(
        winners[[column for column in ["model_name", "delta_vs_baseline", "mean_delta_vs_baseline", "sign_acc_pct", "direction_acc_pct", "robustness_status", "recommendation_bucket"] if column in winners.columns]].head(8),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Suppressed But Interesting")
    suppressed = registry_df.copy()
    if {"raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility"}.issubset(suppressed.columns):
        suppressed = suppressed[
            (pd.to_numeric(suppressed["raw_model_delta_vs_baseline"], errors="coerce") > 0)
            & (
                (pd.to_numeric(suppressed["delta_vs_baseline"], errors="coerce") <= 0)
                | (~suppressed["selection_eligibility"].fillna(False).astype(bool))
            )
        ]
    st.dataframe(
        suppressed[[column for column in ["model_name", "raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility", "overfit_status", "guarded_candidate_type"] if column in suppressed.columns]].head(8),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Model Registry")
    render_model_registry_table(registry_df)