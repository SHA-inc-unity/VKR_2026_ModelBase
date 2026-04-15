from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from catboost_floader.core.config import OUTPUT_DIR, REPORT_DIR
from catboost_floader.frontend_api.action_requests import get_action_catalog
from catboost_floader.frontend_api.dashboard_queries import get_dashboard_overview
from catboost_floader.frontend_api.job_queries import get_recent_jobs
from catboost_floader.frontend_api.model_detail_queries import get_model_detail


def _utc_timestamp(generated_at: Any = None) -> str:
    if generated_at is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if hasattr(generated_at, "strftime"):
        return generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(generated_at)


def _fmt_text(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return str(value)


def _fmt_bool(value: Any) -> str:
    if value is None:
        return "-"
    return "Yes" if bool(value) else "No"


def _fmt_number(value: Any, digits: int = 2, signed: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if signed else ""
    return f"{numeric:{sign},.{digits}f}"


def _fmt_percent_auto(value: Any, digits: int = 2) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(numeric) <= 1.0:
        numeric *= 100.0
    return f"{numeric:.{digits}f}%"


def _registry_frame() -> pd.DataFrame:
    overview = get_dashboard_overview()
    return pd.DataFrame([entry.to_dict() for entry in overview.registry])


def _sorted_registry(registry_df: pd.DataFrame | None) -> pd.DataFrame:
    if registry_df is None or registry_df.empty:
        return pd.DataFrame()
    view = registry_df.copy()
    view["selection_eligibility"] = view.get("selection_eligibility", False).fillna(False).astype(bool)
    for column in ["delta_vs_baseline", "mean_delta_vs_baseline", "sign_acc_pct", "direction_acc_pct"]:
        view[column] = pd.to_numeric(view.get(column), errors="coerce")
    return view.sort_values(
        by=["selection_eligibility", "delta_vs_baseline", "mean_delta_vs_baseline", "sign_acc_pct", "direction_acc_pct"],
        ascending=[False, False, False, False, False],
        na_position="last",
    )


def choose_best_model_entry(registry_df: pd.DataFrame | None) -> dict[str, Any] | None:
    sorted_registry = _sorted_registry(registry_df)
    if sorted_registry.empty:
        return None
    return sorted_registry.iloc[0].to_dict()


def _table_to_text(frame: pd.DataFrame, *, columns: list[str], limit: int = 8) -> str:
    if frame is None or frame.empty:
        return "(none)"
    available_columns = [column for column in columns if column in frame.columns]
    if not available_columns:
        return "(none)"
    view = frame[available_columns].head(limit).copy().fillna("-")
    return view.to_string(index=False)


def build_dashboard_txt_report(*, selected_model_key: str | None, generated_at: Any = None) -> str:
    registry_df = _sorted_registry(_registry_frame())
    selected_model = get_model_detail(str(selected_model_key or "main_direct_pipeline"))
    selected_record = selected_model.to_dict() if selected_model is not None else {}
    selected_summary = dict(selected_record.get("summary", {}) or {})
    selected_selection = dict(selected_record.get("selection", {}) or {})
    selected_overfit = dict(selected_record.get("overfitting", {}) or {})
    selected_robustness = dict(selected_record.get("robustness", {}) or {})
    selected_registry = dict(selected_record.get("registry", {}) or {})
    eligible_df = registry_df[registry_df["selection_eligibility"]] if not registry_df.empty and "selection_eligibility" in registry_df.columns else pd.DataFrame()
    overfit_df = registry_df[registry_df.get("overfit_status", "").fillna("").isin(["moderate", "severe"])] if not registry_df.empty and "overfit_status" in registry_df.columns else pd.DataFrame()
    best_model = choose_best_model_entry(registry_df)
    recent_jobs = [job.to_dict() for job in get_recent_jobs(limit=5, max_log_lines=10)]
    action_catalog = get_action_catalog(selected_model_key)

    lines: list[str] = []
    lines.append("MODEL DASHBOARD REPORT")
    lines.append(f"Generated: {_utc_timestamp(generated_at)}")
    lines.append("")
    lines.append("System Summary")
    lines.append("--------------")
    lines.append(f"Outputs directory: {_fmt_text(OUTPUT_DIR)}")
    lines.append(f"Report directory: {_fmt_text(REPORT_DIR)}")
    lines.append(f"Total models: {int(len(registry_df))}")
    lines.append(f"Eligible models: {int(len(eligible_df))}")
    lines.append(
        f"Robust models: {int(registry_df['robustness_status'].fillna('').astype(str).str.startswith('robust').sum()) if not registry_df.empty and 'robustness_status' in registry_df.columns else 0}"
    )
    lines.append(
        f"Overfit risk models: {int(overfit_df.shape[0])}"
    )

    lines.append("")
    lines.append("Best Model")
    lines.append("----------")
    if best_model:
        lines.append(f"Name: {_fmt_text(best_model.get('model_name'))}")
        lines.append(f"Key: {_fmt_text(best_model.get('model_key'))}")
        lines.append(f"Recommendation: {_fmt_text(best_model.get('recommendation_bucket'))}")
        lines.append(f"Eligible: {_fmt_bool(best_model.get('selection_eligibility'))}")
        lines.append(f"Delta vs baseline: {_fmt_number(best_model.get('delta_vs_baseline'), signed=True)}")
        lines.append(f"Robustness: {_fmt_text(best_model.get('robustness_status'))}")
        lines.append(f"Overfit status: {_fmt_text(best_model.get('overfit_status'))}")
    else:
        lines.append("No model registry data available.")

    lines.append("")
    lines.append("Active / Eligible Models")
    lines.append("------------------------")
    if eligible_df.empty:
        lines.append("No eligible models detected.")
    else:
        for _, row in eligible_df.head(10).iterrows():
            lines.append(
                "- "
                f"{_fmt_text(row.get('model_name'))} | "
                f"Delta {_fmt_number(row.get('delta_vs_baseline'), signed=True)} | "
                f"Robustness {_fmt_text(row.get('robustness_status'))} | "
                f"Recommendation {_fmt_text(row.get('recommendation_bucket'))}"
            )

    lines.append("")
    lines.append("Key Metrics Table")
    lines.append("-----------------")
    lines.append(
        _table_to_text(
            registry_df,
            columns=[
                "model_name",
                "recommendation_bucket",
                "robustness_status",
                "selection_eligibility",
                "delta_vs_baseline",
                "mean_delta_vs_baseline",
                "sign_acc_pct",
                "direction_acc_pct",
                "overfit_status",
            ],
            limit=10,
        )
    )

    lines.append("")
    lines.append("Overfit Summary")
    lines.append("---------------")
    if overfit_df.empty:
        lines.append("No moderate or severe overfit flags detected.")
    else:
        lines.append(
            _table_to_text(
                overfit_df,
                columns=["model_name", "overfit_status", "overfit_reason", "delta_vs_baseline", "recommendation_bucket"],
                limit=10,
            )
        )

    if selected_record:
        lines.append("")
        lines.append("Selected Model Details")
        lines.append("----------------------")
        lines.append(f"Selected key: {_fmt_text(selected_record.get('model_key'))}")
        lines.append(f"Selected name: {_fmt_text(selected_record.get('model_name'))}")
        lines.append(f"Recommendation: {_fmt_text(selected_registry.get('recommendation_bucket'))}")
        lines.append(f"MAE: {_fmt_number(selected_summary.get('MAE'))}")
        lines.append(f"Delta vs baseline: {_fmt_number(selected_summary.get('delta_vs_baseline'), signed=True)}")
        lines.append(f"Robustness: {_fmt_text(selected_summary.get('robustness_status'))}")
        lines.append(f"Eligible: {_fmt_bool(selected_summary.get('selection_eligibility'))}")
        lines.append(f"Overfit status: {_fmt_text(selected_summary.get('overfit_status'))}")

        lines.append("")
        lines.append("Selection Trace")
        lines.append("---------------")
        lines.append(f"Selected candidate: {_fmt_text(selected_selection.get('selected_candidate_type'))}")
        lines.append(f"Guarded candidate: {_fmt_text(selected_selection.get('guarded_candidate_type'))}")
        lines.append(f"Raw candidate: {_fmt_text(selected_selection.get('raw_model_candidate_type'))}")
        lines.append(f"Ranking reason: {_fmt_text(selected_selection.get('main_selection_final_ranking_reason'))}")
        lines.append(f"Holdout guard reason: {_fmt_text(selected_selection.get('final_holdout_guard_reason'))}")

        lines.append("")
        lines.append("Robustness Snapshot")
        lines.append("-------------------")
        lines.append(f"Mean delta vs baseline: {_fmt_number(selected_robustness.get('mean_delta_vs_baseline'), signed=True)}")
        lines.append(f"Std delta vs baseline: {_fmt_number(selected_robustness.get('std_delta_vs_baseline'))}")
        lines.append(f"Win rate vs baseline: {_fmt_percent_auto(selected_robustness.get('win_rate_vs_baseline'))}")
        lines.append(f"Mean sign accuracy: {_fmt_percent_auto(selected_robustness.get('mean_sign_accuracy_pct'))}")

        lines.append("")
        lines.append("Overfitting Snapshot")
        lines.append("--------------------")
        lines.append(f"Overfit status: {_fmt_text(selected_overfit.get('overfit_status'))}")
        lines.append(f"Overfit reason: {_fmt_text(selected_overfit.get('overfit_reason'))}")
        lines.append(f"Train MAE: {_fmt_number(selected_overfit.get('train_MAE'))}")
        lines.append(f"Holdout MAE: {_fmt_number(selected_overfit.get('holdout_MAE'))}")
        lines.append(f"Holdout overfit ratio: {_fmt_number(selected_overfit.get('holdout_overfit_ratio'))}")

    lines.append("")
    lines.append("Practical Notes / Status")
    lines.append("------------------------")
    lines.append(f"Focused model: {_fmt_text(selected_model_key)}")
    lines.append(f"Run all models control: {_fmt_text(action_catalog.get('run_all_models', {}).get('summary'))}")
    lines.append(f"Run selected model control: {_fmt_text(action_catalog.get('run_selected_model', {}).get('summary'))}")
    lines.append(f"Refresh control: {_fmt_text(action_catalog.get('refresh_artifacts', {}).get('summary'))}")

    lines.append("")
    lines.append("Recent Job Status")
    lines.append("-----------------")
    if not recent_jobs:
        lines.append("No backend jobs have been recorded yet.")
    else:
        for job in recent_jobs:
            lines.append(
                "- "
                f"{_fmt_text(job.get('label'))} | "
                f"Status {_fmt_text(job.get('status'))} | "
                f"Created {_fmt_text(job.get('created_at'))} | "
                f"Target {_fmt_text(job.get('target_model'))}"
            )
            if job.get("summary"):
                lines.append(f"  Summary: {_fmt_text(job.get('summary'))}")
            if job.get("error_message"):
                lines.append(f"  Error: {_fmt_text(job.get('error_message'))}")

    return "\n".join(lines)