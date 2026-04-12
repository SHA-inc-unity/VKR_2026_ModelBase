from __future__ import annotations

import html
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from catboost_floader.frontend_api.action_requests import dispatch_action_request
from catboost_floader.frontend_api.dashboard_queries import get_dashboard_overview
from catboost_floader.frontend_api.job_queries import get_recent_jobs
from catboost_floader.frontend_api.model_detail_queries import get_model_detail
from catboost_floader.frontend_api.report_queries import build_dashboard_txt_report

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
from frontend.components.model_analysis import (
    render_model_artifacts_section,
    render_model_overfitting_section,
    render_model_robustness_section,
    render_model_selection_section,
    render_model_summary_section,
)
from frontend.components.tables import render_anomalies_table, render_backtest_table, render_market_table
from frontend.services.formatters import fmt_bool, fmt_confidence, fmt_delta, fmt_number, fmt_percent, fmt_price, fmt_text
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
    load_model_registry,
    load_pipeline_summary,
)
from frontend.services.reporting import choose_best_model


def _format_model_option(model_key: str) -> str:
    return "Main Pipeline" if model_key == "main_direct_pipeline" else str(model_key)


def _inject_dashboard_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        .dashboard-kicker {
            margin: 0;
            color: #8ea4ca;
            font-size: 0.72rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            font-weight: 700;
        }
        .dashboard-hero {
            border: 1px solid rgba(76, 201, 240, 0.18);
            border-radius: 22px;
            padding: 1.2rem 1.25rem;
            margin-bottom: 1rem;
            background:
                radial-gradient(circle at top right, rgba(76, 201, 240, 0.14), transparent 34%),
                radial-gradient(circle at bottom left, rgba(34, 197, 94, 0.12), transparent 32%),
                linear-gradient(180deg, rgba(16, 22, 34, 0.98), rgba(8, 12, 22, 0.98));
        }
        .dashboard-hero h1 {
            margin: 0.18rem 0 0.45rem 0;
            color: #f7fafc;
            font-size: 2rem;
            line-height: 1.1;
        }
        .dashboard-hero p {
            margin: 0;
            color: #c6d2e5;
            line-height: 1.5;
        }
        .dashboard-card {
            min-height: 138px;
            border-radius: 18px;
            padding: 1rem 1.05rem;
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: linear-gradient(180deg, rgba(17, 24, 39, 0.98), rgba(8, 13, 22, 0.98));
            box-shadow: 0 14px 34px rgba(0, 0, 0, 0.18);
        }
        .dashboard-card.positive {
            border-color: rgba(34, 197, 94, 0.35);
            background: linear-gradient(180deg, rgba(9, 29, 24, 0.98), rgba(6, 18, 17, 0.98));
        }
        .dashboard-card.warning {
            border-color: rgba(245, 158, 11, 0.35);
            background: linear-gradient(180deg, rgba(44, 28, 10, 0.98), rgba(24, 16, 8, 0.98));
        }
        .dashboard-card.danger {
            border-color: rgba(248, 113, 113, 0.35);
            background: linear-gradient(180deg, rgba(44, 18, 20, 0.98), rgba(22, 10, 14, 0.98));
        }
        .dashboard-card.accent {
            border-color: rgba(56, 189, 248, 0.35);
            background: linear-gradient(180deg, rgba(13, 28, 40, 0.98), rgba(9, 18, 27, 0.98));
        }
        .dashboard-card-label {
            color: #8ea4ca;
            font-size: 0.76rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }
        .dashboard-card-value {
            color: #f8fbff;
            font-size: 1.55rem;
            font-weight: 700;
            line-height: 1.1;
            margin-bottom: 0.55rem;
        }
        .dashboard-card-note {
            color: #cad5e5;
            font-size: 0.92rem;
            line-height: 1.45;
        }
        .dashboard-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.65rem;
        }
        .dashboard-pill {
            padding: 0.28rem 0.65rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 700;
            border: 1px solid rgba(148, 163, 184, 0.24);
            color: #e2e8f0;
            background: rgba(30, 41, 59, 0.8);
        }
        .dashboard-pill.positive {
            border-color: rgba(34, 197, 94, 0.35);
            background: rgba(20, 83, 45, 0.42);
        }
        .dashboard-pill.warning {
            border-color: rgba(245, 158, 11, 0.35);
            background: rgba(146, 64, 14, 0.42);
        }
        .dashboard-pill.danger {
            border-color: rgba(248, 113, 113, 0.35);
            background: rgba(127, 29, 29, 0.42);
        }
        .dashboard-pill.accent {
            border-color: rgba(56, 189, 248, 0.35);
            background: rgba(14, 116, 144, 0.42);
        }
        .dashboard-section-caption {
            color: #95a8ca;
            margin-bottom: 0.65rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_status_card(label: str, value: str, note: str, *, tone: str = "accent") -> str:
    return (
        f"<div class='dashboard-card {tone}'>"
        f"<div class='dashboard-card-label'>{html.escape(label)}</div>"
        f"<div class='dashboard-card-value'>{html.escape(value)}</div>"
        f"<div class='dashboard-card-note'>{html.escape(note)}</div>"
        "</div>"
    )


def _render_status_cards(cards: list[dict[str, str]]) -> None:
    if not cards:
        return
    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        column.markdown(
            _render_status_card(
                card.get("label", "Status"),
                card.get("value", "-"),
                card.get("note", "-"),
                tone=card.get("tone", "accent"),
            ),
            unsafe_allow_html=True,
        )


def _pill(text: str, *, tone: str = "accent") -> str:
    return f"<span class='dashboard-pill {tone}'>{html.escape(text)}</span>"


def _filter_registry(
    registry_df: pd.DataFrame,
    *,
    only_eligible: bool,
    only_robust: bool,
    only_overfit_risk: bool,
    only_positive_delta: bool,
    search_text: str,
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
    needle = search_text.strip().lower()
    if needle:
        filtered = filtered[
            filtered["model_name"].fillna("").astype(str).str.lower().str.contains(needle)
            | filtered["model_key"].fillna("").astype(str).str.lower().str.contains(needle)
        ]
    return filtered.reset_index(drop=True)


def _format_registry_view(registry_df: pd.DataFrame, selected_model_key: str | None) -> pd.DataFrame:
    if registry_df is None or registry_df.empty:
        return pd.DataFrame()

    view = registry_df.copy()
    view.insert(0, "Focus", view["model_key"].eq(selected_model_key).map(lambda is_selected: "Selected" if is_selected else ""))
    for column in ["selection_eligibility", "raw_model_used_before_guard", "guarded_candidate_after_guard", "is_main"]:
        if column in view.columns:
            view[column] = view[column].map(fmt_bool)
    for column in ["delta_vs_baseline", "raw_model_delta_vs_baseline", "mean_delta_vs_baseline", "std_delta_vs_baseline"]:
        if column in view.columns:
            view[column] = view[column].map(fmt_delta)
    for column in ["sign_acc_pct", "direction_acc_pct", "raw_model_sign_acc_pct", "raw_model_direction_acc_pct"]:
        if column in view.columns:
            view[column] = view[column].map(lambda value: fmt_percent(value, scale_100=False))
    if "win_rate_vs_baseline" in view.columns:
        view["win_rate_vs_baseline"] = view["win_rate_vs_baseline"].map(lambda value: fmt_percent(value, scale_100=True))

    preferred = [
        "Focus",
        "model_name",
        "recommendation_bucket",
        "robustness_status",
        "selection_eligibility",
        "delta_vs_baseline",
        "mean_delta_vs_baseline",
        "win_rate_vs_baseline",
        "sign_acc_pct",
        "direction_acc_pct",
        "overfit_status",
        "guarded_candidate_type",
    ]
    return view[[column for column in preferred if column in view.columns]]


def _render_scalar_dict(title: str, payload: dict[str, Any], *, preferred_order: list[str] | None = None) -> None:
    if not payload:
        st.info(f"No {title.lower()} available.")
        return

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    ordered_keys = list(preferred_order or []) + [key for key in payload.keys() if key not in set(preferred_order or [])]
    for key in ordered_keys:
        if key in seen or key not in payload or isinstance(payload.get(key), (dict, list)):
            continue
        seen.add(key)
        value = payload.get(key)
        if isinstance(value, bool):
            rendered = fmt_bool(value)
        elif isinstance(value, (int, float)):
            rendered = fmt_number(value, digits=4 if abs(float(value)) < 1 else 2)
        else:
            rendered = fmt_text(value)
        rows.append({"Field": key.replace("_", " ").title(), "Value": rendered})

    if not rows:
        st.info(f"No {title.lower()} available.")
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _render_fleet_comparison_chart(registry_df: pd.DataFrame) -> None:
    if registry_df is None or registry_df.empty or "delta_vs_baseline" not in registry_df.columns:
        st.info("No comparison chart available.")
        return

    chart_df = registry_df[["model_name", "delta_vs_baseline"]].copy()
    chart_df["delta_vs_baseline"] = pd.to_numeric(chart_df["delta_vs_baseline"], errors="coerce")
    chart_df = chart_df.dropna(subset=["delta_vs_baseline"]).head(10)
    if chart_df.empty:
        st.info("No numeric delta values available for comparison.")
        return

    fig = go.Figure(
        go.Bar(
            x=chart_df["model_name"],
            y=chart_df["delta_vs_baseline"],
            marker_color=["#22c55e" if value > 0 else "#f97316" for value in chart_df["delta_vs_baseline"]],
            text=chart_df["delta_vs_baseline"].map(lambda value: f"{value:+.2f}"),
            textposition="outside",
        )
    )
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Delta vs Baseline")
    st.plotly_chart(fig, use_container_width=True)


def _render_selected_model_header(record: dict[str, Any] | None) -> None:
    if not record:
        st.info("Choose a model from the control bar to inspect it here.")
        return

    summary = dict(record.get("summary", {}) or {})
    selection = dict(record.get("selection", {}) or {})
    registry = dict(record.get("registry", {}) or {})

    pills = [
        _pill(f"Robustness: {fmt_text(summary.get('robustness_status'))}", tone="positive" if str(summary.get("robustness_status", "")).startswith("robust") else "accent"),
        _pill(f"Eligible: {fmt_bool(summary.get('selection_eligibility'))}", tone="positive" if summary.get("selection_eligibility") else "warning"),
        _pill(f"Overfit: {fmt_text(summary.get('overfit_status'))}", tone="danger" if str(summary.get("overfit_status", "")).lower() in {"moderate", "severe"} else "accent"),
        _pill(f"Recommendation: {fmt_text(registry.get('recommendation_bucket'))}", tone="accent"),
    ]
    if selection.get("guarded_candidate_type"):
        pills.append(_pill(f"Candidate: {fmt_text(selection.get('guarded_candidate_type'))}", tone="warning"))

    st.markdown(
        """
        <div class="dashboard-hero" style="margin-bottom:0.65rem;">
            <p class="dashboard-kicker">Focused Model</p>
            <h1>{name}</h1>
            <p>{subtitle}</p>
            <div class="dashboard-pills">{pills}</div>
        </div>
        """.format(
            name=html.escape(str(record.get("model_name", record.get("model_key", "Model")))),
            subtitle=html.escape(
                f"Guarded delta {fmt_delta(summary.get('delta_vs_baseline'))} | "
                f"Raw delta {fmt_delta(dict(record.get('raw_model', {}) or {}).get('raw_model_delta_vs_baseline'))}"
            ),
            pills="".join(pills),
        ),
        unsafe_allow_html=True,
    )


def _render_watchlist_table(frame: pd.DataFrame, *, columns: list[str], empty_message: str) -> None:
    if frame is None or frame.empty:
        st.info(empty_message)
        return
    view = frame[[column for column in columns if column in frame.columns]].copy().fillna("-")
    st.dataframe(view, width="stretch", hide_index=True)


def _format_job_view(jobs: list[dict[str, Any]], selected_model_key: str | None) -> pd.DataFrame:
    if not jobs:
        return pd.DataFrame()

    rows: list[dict[str, str]] = []
    for job in jobs:
        target_model = job.get("target_model")
        scope = "Focused" if selected_model_key and target_model == selected_model_key else "Fleet"
        if not target_model and job.get("action_type") != "run_all_models":
            scope = "Global"
        rows.append(
            {
                "Job": str(job.get("job_id", "-")),
                "Action": fmt_text(job.get("label")),
                "Status": fmt_text(job.get("status")),
                "Scope": scope,
                "Target": fmt_text(target_model or "all models"),
                "Created": fmt_text(job.get("created_at")),
                "Started": fmt_text(job.get("started_at")),
                "Finished": fmt_text(job.get("finished_at")),
                "Summary": fmt_text(job.get("summary") or job.get("error_message")),
            }
        )
    return pd.DataFrame(rows)


def _pick_featured_job(jobs: list[dict[str, Any]], selected_model_key: str | None) -> dict[str, Any] | None:
    if not jobs:
        return None

    for job in jobs:
        if job.get("status") in {"running", "queued"} and job.get("target_model") in {None, selected_model_key}:
            return job
    for job in jobs:
        if job.get("target_model") in {None, selected_model_key}:
            return job
    return jobs[0]


def render_dashboard() -> None:
    st.set_page_config(
        page_title="Model Workspace",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_dashboard_css()

    dashboard_overview = get_dashboard_overview()
    registry_df = pd.DataFrame([entry.to_dict() for entry in dashboard_overview.registry])
    if registry_df.empty:
        registry_df = load_model_registry()
    model_keys = registry_df["model_key"].tolist() if not registry_df.empty and "model_key" in registry_df.columns else list_model_keys()
    if not model_keys:
        model_keys = ["main_direct_pipeline"]

    available_files = list_market_files()
    if st.session_state.get("dashboard_selected_model") not in model_keys:
        st.session_state["dashboard_selected_model"] = model_keys[0]
    if available_files and st.session_state.get("dashboard_market_file") not in available_files:
        st.session_state["dashboard_market_file"] = available_files[-1]
    if not available_files:
        st.session_state["dashboard_market_file"] = None

    selected_model_key = st.session_state.get("dashboard_selected_model", model_keys[0])
    selected_model_detail = get_model_detail(selected_model_key)
    selected_model_record = selected_model_detail.to_dict() if selected_model_detail is not None else None

    market_file = st.session_state.get("dashboard_market_file")
    market_df = load_market_data(market_file) if market_file else pd.DataFrame()
    market_summary = compute_market_summary(market_df) if not market_df.empty else {}
    anomaly_windows_df = load_anomaly_windows()
    selected_backtest_df = load_backtest_results(selected_model_key)
    selected_backtest_summary = load_backtest_summary(selected_model_key)
    selected_feature_importance = load_feature_importance(selected_model_key)
    live_snapshot = load_live_snapshot()
    pipeline_summary = load_pipeline_summary()
    latest_prediction = live_snapshot if selected_model_key == "main_direct_pipeline" and live_snapshot else get_latest_prediction(selected_backtest_df)
    frontend_paths = get_frontend_paths()
    report_text = build_dashboard_txt_report(selected_model_key=selected_model_key)
    report_name = f"model_dashboard_report_{pd.Timestamp.utcnow().strftime('%Y%m%d_%H%M%SZ')}.txt"
    recent_jobs = [job.to_dict() for job in get_recent_jobs(limit=8, max_log_lines=16)]

    st.markdown(
        """
        <div class="dashboard-hero">
            <p class="dashboard-kicker">Unified Workspace</p>
            <h1>Model Analysis Control Center</h1>
            <p>
                One main working screen for fleet health, selected-model drilldown, chart inspection,
                reserved run controls, and plain-text reporting. No page hopping required.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    control_cols = st.columns([2.0, 1.8, 1.15, 1.35, 0.95, 1.45])
    with control_cols[0]:
        st.selectbox(
            "Focus model",
            options=model_keys,
            key="dashboard_selected_model",
            format_func=_format_model_option,
        )
    with control_cols[1]:
        if available_files:
            st.selectbox(
                "Market dataset",
                options=available_files,
                key="dashboard_market_file",
            )
        else:
            st.caption("Market dataset")
            st.write("No cached dataset")
    with control_cols[2]:
        run_all_clicked = st.button("Run all models", type="primary", use_container_width=True)
    with control_cols[3]:
        run_selected_clicked = st.button("Run selected model", type="primary", use_container_width=True)
    with control_cols[4]:
        refresh_clicked = st.button("Refresh", use_container_width=True)
    with control_cols[5]:
        st.download_button(
            "Export TXT report",
            data=report_text,
            file_name=report_name,
            mime="text/plain",
            use_container_width=True,
        )

    if run_all_clicked:
        st.session_state["dashboard_action_event"] = dispatch_action_request("run_all_models", selected_model_key).to_dict()
        st.rerun()
    if run_selected_clicked:
        st.session_state["dashboard_action_event"] = dispatch_action_request("run_selected_model", selected_model_key).to_dict()
        st.rerun()
    if refresh_clicked:
        st.session_state["dashboard_action_event"] = dispatch_action_request("refresh_artifacts", selected_model_key).to_dict()
        st.cache_data.clear()
        st.rerun()

    with st.expander("View options and data source", expanded=False):
        option_cols = st.columns([1.1, 1.1, 2.0])
        with option_cols[0]:
            rows_to_show = st.slider("Rows to show", min_value=200, max_value=5000, value=1000, step=100)
        with option_cols[1]:
            show_anomalies = st.toggle("Show anomalies", value=True)
            show_range = st.toggle("Show range", value=True)
        with option_cols[2]:
            st.caption("Artifact source")
            st.write(frontend_paths.get("outputs_dir", "-"))
            st.caption("Report directory")
            st.write(frontend_paths.get("report_dir", "-"))
    if "rows_to_show" not in locals():
        rows_to_show = 1000
        show_anomalies = True
        show_range = True

    action_event = dict(st.session_state.get("dashboard_action_event", {}) or {})
    if action_event:
        tone = action_event.get("tone", "info")
        job_event = dict(action_event.get("job", {}) or {})
        message = action_event.get("message", "")
        if job_event:
            message = f"{message} Job {job_event.get('job_id', '-')} is {job_event.get('status', 'queued')}."
        if tone == "warning":
            st.warning(message)
        elif tone == "error":
            st.error(message)
        elif tone == "success":
            st.success(message)
        else:
            st.info(message)
    st.caption(
        "Run controls on this screen queue tracked backend jobs. Use the jobs panel below to inspect status, timestamps, and recent log output without leaving the dashboard."
    )

    job_left, job_right = st.columns([1.25, 1.0], gap="large")
    with job_left:
        st.subheader("Backend Jobs")
        st.caption("Recent actions are persisted to the backend job registry. Running jobs update on refresh or rerun.")
        job_view = _format_job_view(recent_jobs, selected_model_key)
        if job_view.empty:
            st.info("No backend jobs recorded yet.")
        else:
            st.dataframe(job_view, width="stretch", hide_index=True)
    with job_right:
        st.subheader("Latest Log Tail")
        featured_job = _pick_featured_job(recent_jobs, selected_model_key)
        if not featured_job:
            st.info("No job log available yet.")
        else:
            st.caption(
                f"{fmt_text(featured_job.get('label'))} | status {fmt_text(featured_job.get('status'))} | created {fmt_text(featured_job.get('created_at'))}"
            )
            log_lines = list(featured_job.get("latest_log_lines", []) or [])
            if log_lines:
                st.code("\n".join(log_lines), language="text")
            else:
                st.info("The selected job has no log lines yet.")
            if featured_job.get("log_path"):
                st.caption(f"Log file: {featured_job.get('log_path')}")

    best_model = choose_best_model(registry_df)
    eligible_count = dashboard_overview.eligible_count if dashboard_overview.total_models else (int(registry_df["selection_eligibility"].fillna(False).astype(bool).sum()) if not registry_df.empty and "selection_eligibility" in registry_df.columns else 0)
    robust_count = dashboard_overview.robust_count if dashboard_overview.total_models else (int(registry_df["robustness_status"].fillna("").astype(str).str.startswith("robust").sum()) if not registry_df.empty and "robustness_status" in registry_df.columns else 0)
    positive_delta_count = dashboard_overview.positive_delta_count if dashboard_overview.total_models else (int((pd.to_numeric(registry_df.get("delta_vs_baseline"), errors="coerce") > 0).sum()) if not registry_df.empty and "delta_vs_baseline" in registry_df.columns else 0)
    overfit_risk_count = dashboard_overview.overfit_risk_count if dashboard_overview.total_models else (int(registry_df["overfit_status"].fillna("").isin(["moderate", "severe"]).sum()) if not registry_df.empty and "overfit_status" in registry_df.columns else 0)
    suppressed_edge_count = 0
    if not registry_df.empty and {"raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility"}.issubset(registry_df.columns):
        suppressed_edge_count = int(
            (
                (pd.to_numeric(registry_df["raw_model_delta_vs_baseline"], errors="coerce") > 0)
                & (
                    (pd.to_numeric(registry_df["delta_vs_baseline"], errors="coerce") <= 0)
                    | (~registry_df["selection_eligibility"].fillna(False).astype(bool))
                )
            ).sum()
        )

    selected_summary = dict((selected_model_record or {}).get("summary", {}) or {})
    selected_selection = dict((selected_model_record or {}).get("selection", {}) or {})
    selected_registry = dict((selected_model_record or {}).get("registry", {}) or {})
    _render_status_cards(
        [
            {
                "label": "Best model",
                "value": fmt_text((best_model or {}).get("model_name")),
                "note": (
                    f"Delta {fmt_delta((best_model or {}).get('delta_vs_baseline'))} | "
                    f"{fmt_text((best_model or {}).get('recommendation_bucket'))}"
                ),
                "tone": "positive" if (best_model or {}).get("selection_eligibility") else "accent",
            },
            {
                "label": "Eligible fleet",
                "value": f"{eligible_count} / {len(registry_df)}",
                "note": f"Robust {robust_count} | Positive delta {positive_delta_count}",
                "tone": "accent",
            },
            {
                "label": "Overfit watch",
                "value": str(overfit_risk_count),
                "note": f"Suppressed edge {suppressed_edge_count}",
                "tone": "danger" if overfit_risk_count else "warning",
            },
            {
                "label": "Focused model",
                "value": fmt_text((selected_model_record or {}).get("model_name")),
                "note": (
                    f"{fmt_text(selected_registry.get('recommendation_bucket'))} | "
                    f"Candidate {fmt_text(selected_selection.get('guarded_candidate_type') or selected_selection.get('selected_candidate_type'))}"
                ),
                "tone": "accent",
            },
            {
                "label": "Prediction confidence",
                "value": fmt_confidence((latest_prediction or {}).get("confidence")),
                "note": f"Latest anomaly {fmt_text((latest_prediction or {}).get('anomaly_level') or (latest_prediction or {}).get('anomaly_type'))}",
                "tone": "warning" if latest_prediction and float((latest_prediction or {}).get("confidence", 0) or 0) < 0.5 else "positive",
            },
            {
                "label": "Market snapshot",
                "value": fmt_price(market_summary.get("last_close")),
                "note": f"24h change {fmt_percent(market_summary.get('change_24h'), scale_100=True)} | Rows {fmt_text(market_summary.get('rows'))}",
                "tone": "accent",
            },
        ]
    )

    fleet_col, focus_col = st.columns([1.08, 0.92], gap="large")
    with fleet_col:
        st.subheader("Model Fleet")
        st.caption("Filter the full registry here, compare health signals, then inspect the focused model on the right.")

        filter_cols = st.columns([1, 1, 1, 1, 1.45])
        only_eligible = filter_cols[0].checkbox("Only eligible", value=False, key="dashboard_filter_eligible")
        only_robust = filter_cols[1].checkbox("Only robust", value=False, key="dashboard_filter_robust")
        only_overfit_risk = filter_cols[2].checkbox("Only overfit risk", value=False, key="dashboard_filter_overfit")
        only_positive_delta = filter_cols[3].checkbox("Only positive delta", value=False, key="dashboard_filter_positive")
        search_text = filter_cols[4].text_input("Search", value="", placeholder="Model name or key", key="dashboard_filter_search")

        filtered_registry = _filter_registry(
            registry_df,
            only_eligible=only_eligible,
            only_robust=only_robust,
            only_overfit_risk=only_overfit_risk,
            only_positive_delta=only_positive_delta,
            search_text=search_text,
        )
        st.caption(f"Showing {len(filtered_registry)} of {len(registry_df)} models. Use the control bar to change the focused model.")
        fleet_view = _format_registry_view(filtered_registry, selected_model_key)
        if fleet_view.empty:
            st.info("No models match the current filters.")
        else:
            st.dataframe(fleet_view, width="stretch", hide_index=True)

        st.subheader("Delta Comparison")
        _render_fleet_comparison_chart(filtered_registry)

        if selected_model_key and not filtered_registry.empty and selected_model_key not in filtered_registry["model_key"].tolist():
            st.info("The focused model is currently outside the filtered fleet view.")

    with focus_col:
        _render_selected_model_header(selected_model_record)
        if selected_model_record:
            detail_tabs = st.tabs(["Snapshot", "Selection Trace", "Overfitting", "Robustness", "Advanced"])
            with detail_tabs[0]:
                render_model_summary_section(selected_model_record)
            with detail_tabs[1]:
                render_model_selection_section(selected_model_record, show_advanced=False)
            with detail_tabs[2]:
                render_model_overfitting_section(selected_model_record, show_advanced=False)
            with detail_tabs[3]:
                render_model_robustness_section(selected_model_record)
            with detail_tabs[4]:
                render_model_artifacts_section(selected_model_record, show_raw=True)

    workspace_tabs = st.tabs(["Market & Forecast", "Backtest & Confidence", "Fleet Diagnostics", "TXT Report"])
    with workspace_tabs[0]:
        render_alerts(anomaly_windows_df, latest_prediction)
        render_market_metrics(market_summary)
        render_prediction_metrics(latest_prediction, selected_backtest_summary)

        st.subheader("Price & Forecast")
        render_price_chart(
            market_df,
            anomaly_windows_df if show_anomalies else None,
            rows_to_show,
            latest_prediction if show_range else None,
        )
        market_left, market_right = st.columns([2.1, 1.0])
        with market_left:
            st.subheader("Recent Market Data")
            render_market_table(market_df)
        with market_right:
            st.subheader("Anomaly Windows")
            render_anomalies_table(anomaly_windows_df)

        if latest_prediction:
            st.subheader("Focused Prediction Snapshot")
            _render_scalar_dict(
                "Prediction Snapshot",
                dict(latest_prediction),
                preferred_order=[
                    "timestamp",
                    "current_close",
                    "direct_pred_price",
                    "direct_pred_return",
                    "range_pred_low",
                    "range_pred_high",
                    "confidence",
                    "anomaly_flag",
                    "anomaly_level",
                ],
            )

    with workspace_tabs[1]:
        chart_left, chart_right = st.columns([1.4, 1.0])
        with chart_left:
            st.subheader("Forecast vs Actual")
            render_backtest_chart(selected_backtest_df, min(rows_to_show, 1200))
        with chart_right:
            st.subheader("Confidence")
            render_confidence_gauge((latest_prediction or {}).get("confidence") if latest_prediction else None)
            render_confidence_chart(selected_backtest_df, min(rows_to_show, 1200))

        extra_left, extra_right = st.columns(2)
        with extra_left:
            st.subheader("Range Coverage")
            render_coverage_chart(selected_backtest_df, min(rows_to_show, 1200))
        with extra_right:
            st.subheader("Error Distribution")
            render_error_histogram(selected_backtest_df)

        st.subheader("Backtest Rows")
        render_backtest_table(selected_backtest_df)

    with workspace_tabs[2]:
        compare_cols = st.columns([1.6, 1.0])
        compare_default = filtered_registry["model_key"].head(min(4, len(filtered_registry))).tolist() if not filtered_registry.empty else []
        if st.session_state.get("dashboard_compare_models"):
            compare_default = [key for key in st.session_state.get("dashboard_compare_models", []) if key in model_keys]
        compare_model_keys = st.multiselect(
            "Models to compare",
            options=model_keys,
            default=compare_default,
            format_func=_format_model_option,
            key="dashboard_compare_models",
        )
        comparison_registry = registry_df[registry_df["model_key"].isin(compare_model_keys)].copy() if compare_model_keys else pd.DataFrame()

        with compare_cols[0]:
            st.subheader("Focused Comparison Table")
            comparison_view = _format_registry_view(comparison_registry, selected_model_key)
            if comparison_view.empty:
                st.info("Choose one or more models to compare here.")
            else:
                st.dataframe(comparison_view, width="stretch", hide_index=True)
                _render_fleet_comparison_chart(comparison_registry)

        with compare_cols[1]:
            st.subheader("Selected Model Feature Importance")
            render_feature_importance(selected_feature_importance, "direct")
            render_feature_importance(selected_feature_importance, "range_low")

        watch_left, watch_right = st.columns(2)
        overfit_watch = registry_df[registry_df["overfit_status"].fillna("").isin(["moderate", "severe"])] if not registry_df.empty and "overfit_status" in registry_df.columns else pd.DataFrame()
        suppressed_watch = pd.DataFrame()
        if not registry_df.empty and {"raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility"}.issubset(registry_df.columns):
            suppressed_watch = registry_df[
                (pd.to_numeric(registry_df["raw_model_delta_vs_baseline"], errors="coerce") > 0)
                & (
                    (pd.to_numeric(registry_df["delta_vs_baseline"], errors="coerce") <= 0)
                    | (~registry_df["selection_eligibility"].fillna(False).astype(bool))
                )
            ]

        with watch_left:
            st.subheader("Overfit Watchlist")
            _render_watchlist_table(
                overfit_watch,
                columns=["model_name", "overfit_status", "overfit_reason", "delta_vs_baseline", "recommendation_bucket"],
                empty_message="No moderate or severe overfit flags detected.",
            )

        with watch_right:
            st.subheader("Suppressed Edge Watchlist")
            _render_watchlist_table(
                suppressed_watch,
                columns=["model_name", "raw_model_delta_vs_baseline", "delta_vs_baseline", "selection_eligibility", "guarded_candidate_type"],
                empty_message="No suppressed-edge models detected.",
            )

        selected_artifacts = dict((selected_model_record or {}).get("artifacts", {}) or {})
        if selected_artifacts.get("comparison_vs_baselines"):
            st.subheader("Selected Model vs Baselines")
            _render_scalar_dict(
                "Comparison vs Baselines",
                dict(selected_artifacts.get("comparison_vs_baselines", {}) or {}),
            )

    with workspace_tabs[3]:
        st.subheader("TXT Report Preview")
        st.caption("This export is plain text and meant for easy sharing in chat or issue threads.")
        st.download_button(
            "Download current TXT report",
            data=report_text,
            file_name=report_name,
            mime="text/plain",
            use_container_width=False,
        )
        st.text_area("TXT report content", value=report_text, height=540, disabled=True)
        with st.expander("Current data source summary"):
            _render_scalar_dict("Frontend Paths", frontend_paths)
            _render_scalar_dict("Pipeline Summary", dict(pipeline_summary or {}), preferred_order=["live_status", "direct_profile", "backtest_points"])