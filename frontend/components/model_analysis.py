from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from frontend.services.formatters import fmt_bool, fmt_delta, fmt_number, fmt_percent, fmt_text


def _render_metrics_row(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)


def _scalar_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(payload or {}).items()
        if not isinstance(value, (dict, list))
    }


def _render_key_value_frame(payload: dict[str, Any], *, labels: dict[str, str] | None = None) -> None:
    labels = labels or {}
    if not payload:
        st.info("No data available.")
        return
    rows = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            continue
        rows.append({"Field": labels.get(key, key), "Value": value})
    if not rows:
        st.info("No scalar fields available.")
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def render_model_registry_table(registry_df: pd.DataFrame) -> None:
    if registry_df is None or registry_df.empty:
        st.info("No model registry data found.")
        return

    view = registry_df.copy()
    for col in ["selection_eligibility", "raw_model_used_before_guard", "guarded_candidate_after_guard", "is_main"]:
        if col in view.columns:
            view[col] = view[col].map(fmt_bool)
    for col in [
        "delta_vs_baseline",
        "raw_model_delta_vs_baseline",
        "mean_delta_vs_baseline",
        "std_delta_vs_baseline",
    ]:
        if col in view.columns:
            view[col] = view[col].map(fmt_delta)
    for col in ["sign_acc_pct", "direction_acc_pct", "raw_model_sign_acc_pct", "raw_model_direction_acc_pct"]:
        if col in view.columns:
            view[col] = view[col].map(lambda value: fmt_percent(value, scale_100=False))
    if "win_rate_vs_baseline" in view.columns:
        view["win_rate_vs_baseline"] = view["win_rate_vs_baseline"].map(lambda value: fmt_percent(value, scale_100=True))

    preferred_columns = [
        "model_name",
        "robustness_status",
        "selection_eligibility",
        "delta_vs_baseline",
        "raw_model_delta_vs_baseline",
        "mean_delta_vs_baseline",
        "std_delta_vs_baseline",
        "win_rate_vs_baseline",
        "sign_acc_pct",
        "direction_acc_pct",
        "overfit_status",
        "recommendation_bucket",
        "guarded_candidate_type",
    ]
    columns = [column for column in preferred_columns if column in view.columns]
    st.dataframe(view[columns], width="stretch", hide_index=True)


def render_model_summary_section(record: dict[str, Any]) -> None:
    summary = dict(record.get("summary", {}) or {})
    raw_model = dict(record.get("raw_model", {}) or {})

    _render_metrics_row(
        [
            ("MAE", fmt_number(summary.get("MAE"))),
            ("RMSE", fmt_number(summary.get("RMSE"))),
            ("MAPE", fmt_number(summary.get("MAPE"))),
            ("Sign Accuracy", fmt_percent(summary.get("sign_acc_pct"), scale_100=False)),
            ("Direction Accuracy", fmt_percent(summary.get("direction_acc_pct"), scale_100=False)),
            ("Delta vs Baseline", fmt_delta(summary.get("delta_vs_baseline"))),
        ]
    )
    _render_metrics_row(
        [
            ("Robustness", fmt_text(summary.get("robustness_status"))),
            ("Eligible", fmt_bool(summary.get("selection_eligibility"))),
            ("Overfit", fmt_text(summary.get("overfit_status"))),
            ("Raw Delta", fmt_delta(raw_model.get("raw_model_delta_vs_baseline"))),
            ("Raw Sign", fmt_percent(raw_model.get("raw_model_sign_acc_pct"), scale_100=False)),
            ("Raw Direction", fmt_percent(raw_model.get("raw_model_direction_acc_pct"), scale_100=False)),
        ]
    )

    comparison_rows = [
        {
            "Metric": "MAE",
            "Guarded": summary.get("MAE"),
            "Raw": raw_model.get("raw_model_MAE"),
        },
        {
            "Metric": "Delta vs Baseline",
            "Guarded": summary.get("delta_vs_baseline"),
            "Raw": raw_model.get("raw_model_delta_vs_baseline"),
        },
        {
            "Metric": "Sign Accuracy %",
            "Guarded": summary.get("sign_acc_pct"),
            "Raw": raw_model.get("raw_model_sign_acc_pct"),
        },
        {
            "Metric": "Direction Accuracy %",
            "Guarded": summary.get("direction_acc_pct"),
            "Raw": raw_model.get("raw_model_direction_acc_pct"),
        },
    ]
    st.subheader("Guarded vs Raw")
    st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)


def render_model_overfitting_section(record: dict[str, Any], *, show_advanced: bool = False) -> None:
    overfitting = dict(record.get("overfitting", {}) or {})
    diagnostics = dict(overfitting.get("diagnostics", {}) or {})

    _render_metrics_row(
        [
            ("Train MAE", fmt_number(overfitting.get("train_MAE"))),
            ("Val MAE", fmt_number(overfitting.get("val_MAE"))),
            ("Holdout MAE", fmt_number(overfitting.get("holdout_MAE"))),
            ("Overfit Status", fmt_text(overfitting.get("overfit_status"))),
            ("Overfit Reason", fmt_text(overfitting.get("overfit_reason"))),
        ]
    )
    _render_metrics_row(
        [
            ("MAE Gap Train-Val", fmt_number(overfitting.get("mae_gap_train_val"))),
            ("MAE Gap Train-Holdout", fmt_number(overfitting.get("mae_gap_train_holdout"))),
            ("Sign Gap Train-Val", fmt_number(overfitting.get("sign_gap_train_val"), digits=4)),
            ("Sign Gap Train-Holdout", fmt_number(overfitting.get("sign_gap_train_holdout"), digits=4)),
            ("Holdout Overfit Ratio", fmt_number(overfitting.get("holdout_overfit_ratio"), digits=4)),
        ]
    )
    st.subheader("Overfitting Fields")
    _render_key_value_frame({key: value for key, value in overfitting.items() if key != "diagnostics"})
    if diagnostics and show_advanced:
        with st.expander("Raw Overfitting Diagnostics JSON"):
            st.json(diagnostics)


def render_model_robustness_section(record: dict[str, Any]) -> None:
    robustness = dict(record.get("robustness", {}) or {})
    artifacts = dict(record.get("artifacts", {}) or {})
    multi_window_summary = dict(artifacts.get("multi_window_summary", {}) or {})

    _render_metrics_row(
        [
            ("Mean Delta", fmt_delta(robustness.get("mean_delta_vs_baseline"))),
            ("Std Delta", fmt_delta(robustness.get("std_delta_vs_baseline"))),
            ("Win Rate", fmt_percent(robustness.get("win_rate_vs_baseline"), scale_100=True)),
            ("Best Window", fmt_delta(robustness.get("best_window_delta_vs_baseline"))),
            ("Worst Window", fmt_delta(robustness.get("worst_window_delta_vs_baseline"))),
            ("Mean Sign %", fmt_percent(robustness.get("mean_sign_accuracy_pct"), scale_100=False)),
        ]
    )
    st.subheader("Robustness Fields")
    _render_key_value_frame(robustness)

    windows = list(multi_window_summary.get("windows", []) or [])
    if windows:
        st.subheader("Multi-window Breakdown")
        st.dataframe(pd.DataFrame(windows), width="stretch", hide_index=True)


def render_model_selection_section(record: dict[str, Any], *, show_advanced: bool = False) -> None:
    selection = dict(record.get("selection", {}) or {})

    _render_metrics_row(
        [
            ("Selected Candidate", fmt_text(selection.get("selected_candidate_type"))),
            ("Raw Candidate", fmt_text(selection.get("raw_model_candidate_type"))),
            ("Guarded Candidate", fmt_text(selection.get("guarded_candidate_type"))),
            ("Raw Used Before Guard", fmt_bool(selection.get("raw_model_used_before_guard"))),
            ("Guard Applied", fmt_bool(selection.get("guarded_candidate_after_guard"))),
            ("Validation MAE", fmt_number(selection.get("validation_mae"))),
        ]
    )
    _render_metrics_row(
        [
            ("Final Ranking Reason", fmt_text(selection.get("main_selection_final_ranking_reason"))),
            ("Guard Reason", fmt_text(selection.get("final_holdout_guard_reason"))),
            ("Selection Pool", fmt_text(selection.get("selection_pool"))),
            ("Profile", fmt_text(selection.get("composition_profile"))),
            ("Relaxed Rule", fmt_bool(selection.get("main_selection_relaxed_rule_applied"))),
            ("Persistence Promotion", fmt_bool(selection.get("main_persistence_promotion_applied"))),
        ]
    )

    st.subheader("Selection Fields")
    scalar_selection = {
        key: value
        for key, value in selection.items()
        if key not in {"profile_evaluations", "main_selection_relaxed_rule", "main_persistence_promotion", "direct_strategy", "final_holdout_candidate_before_guard", "final_holdout_candidate_after_guard"}
        and not isinstance(value, (dict, list))
    }
    _render_key_value_frame(scalar_selection)

    relaxed_rule = dict(selection.get("main_selection_relaxed_rule", {}) or {})
    persistence_promotion = dict(selection.get("main_persistence_promotion", {}) or {})
    if relaxed_rule or persistence_promotion:
        st.subheader("Rule Applications")
        left_col, right_col = st.columns(2)
        with left_col:
            st.caption("Relaxed Selection Rule")
            _render_key_value_frame(_scalar_payload(relaxed_rule))
        with right_col:
            st.caption("Persistence Promotion")
            _render_key_value_frame(_scalar_payload(persistence_promotion))

    candidate_before = dict(selection.get("final_holdout_candidate_before_guard", {}) or {})
    candidate_after = dict(selection.get("final_holdout_candidate_after_guard", {}) or {})
    if candidate_before or candidate_after:
        st.subheader("Guard Transition")
        before_col, after_col = st.columns(2)
        with before_col:
            st.caption("Candidate Before Guard")
            _render_key_value_frame(_scalar_payload(candidate_before))
        with after_col:
            st.caption("Candidate After Guard")
            _render_key_value_frame(_scalar_payload(candidate_after))

    if selection.get("profile_evaluations"):
        st.subheader("Profile Evaluations")
        st.dataframe(pd.DataFrame(selection.get("profile_evaluations", [])), width="stretch", hide_index=True)

    if show_advanced:
        with st.expander("Advanced Selection Payloads"):
            st.json(
                {
                    "main_selection_relaxed_rule": relaxed_rule,
                    "main_persistence_promotion": persistence_promotion,
                    "candidate_before_guard": candidate_before,
                    "candidate_after_guard": candidate_after,
                    "selected_strategy": selection.get("direct_strategy", {}),
                }
            )


def render_model_artifacts_section(record: dict[str, Any], *, show_raw: bool = False) -> None:
    artifact_paths = dict(record.get("artifact_paths", {}) or {})
    artifacts = dict(record.get("artifacts", {}) or {})

    st.subheader("Artifact Paths")
    path_rows = [{"Artifact": key, "Path": value} for key, value in artifact_paths.items()]
    st.dataframe(pd.DataFrame(path_rows), width="stretch", hide_index=True)

    if show_raw:
        expander_order = [
            ("Backtest Summary", "backtest_summary"),
            ("Pipeline Metadata", "pipeline_metadata"),
            ("Multi-window Summary", "multi_window_summary"),
            ("Comparison vs Baselines", "comparison_vs_baselines"),
            ("Pipeline Summary Entry", "pipeline_summary_entry"),
        ]
        for title, key in expander_order:
            with st.expander(title):
                st.json(artifacts.get(key, {}))