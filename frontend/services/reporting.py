from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from catboost_floader.frontend_api.report_queries import build_dashboard_txt_report


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


def _fmt_percent(value: Any, digits: int = 2, scale_100: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if scale_100:
        numeric *= 100.0
    return f"{numeric:.{digits}f}%"


def _sorted_registry(registry_df: pd.DataFrame | None) -> pd.DataFrame:
    if registry_df is None or registry_df.empty:
        return pd.DataFrame()

    view = registry_df.copy()
    if "selection_eligibility" in view.columns:
        view["_eligible_sort"] = view["selection_eligibility"].fillna(False).astype(bool).astype(int)
    else:
        view["_eligible_sort"] = 0
    for column in ["delta_vs_baseline", "mean_delta_vs_baseline", "sign_acc_pct", "direction_acc_pct"]:
        if column in view.columns:
            view[column] = pd.to_numeric(view[column], errors="coerce")
        else:
            view[column] = pd.NA
    return view.sort_values(
        by=["_eligible_sort", "delta_vs_baseline", "mean_delta_vs_baseline", "sign_acc_pct", "direction_acc_pct"],
        ascending=[False, False, False, False, False],
        na_position="last",
    ).drop(columns=["_eligible_sort"])


def choose_best_model(registry_df: pd.DataFrame | None) -> dict[str, Any] | None:
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


def _scalar_table(payload: Mapping[str, Any], *, fields: list[tuple[str, str, str]]) -> str:
    rows: list[dict[str, str]] = []
    for key, label, kind in fields:
        value = payload.get(key)
        if kind == "delta":
            rendered = _fmt_number(value, signed=True)
        elif kind == "number":
            rendered = _fmt_number(value)
        elif kind == "percent":
            rendered = _fmt_percent(value)
        elif kind == "bool":
            rendered = _fmt_bool(value)
        else:
            rendered = _fmt_text(value)
        rows.append({"Field": label, "Value": rendered})
    return pd.DataFrame(rows).to_string(index=False)


def build_txt_report(
    *,
    registry_df: pd.DataFrame | None,
    selected_model_key: str | None,
    selected_model_record: dict[str, Any] | None,
    latest_prediction: dict[str, Any] | None,
    market_summary: Mapping[str, Any] | None,
    action_catalog: Mapping[str, Mapping[str, Any]] | None,
    frontend_paths: Mapping[str, str] | None,
    market_file: str | None = None,
    generated_at: Any = None,
) -> str:
    return build_dashboard_txt_report(selected_model_key=selected_model_key, generated_at=generated_at)