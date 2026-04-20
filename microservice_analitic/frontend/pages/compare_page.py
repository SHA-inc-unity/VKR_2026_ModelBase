"""Страница сравнения обученных моделей (multi-select по сохранённым сессиям)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

_HERE = Path(__file__).resolve()
_WORKSPACE_ROOT = _HERE.parents[2]
_FRONTEND_ROOT = _HERE.parents[1]
for _p in (_WORKSPACE_ROOT, _FRONTEND_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend.model import load_session_result
from backend.model.config import MODELS_DIR
from services.colors import C as _C
from services.i18n import t
from services.ui_components import render_back_button, render_lang_toggle

st.set_page_config(page_title="ModelLine — Compare", layout="wide")

_hcols = st.columns([8, 1])
with _hcols[1]:
    render_lang_toggle(key="compare_lang")

render_back_button("app.py")

st.title(t("compare.title"))
st.caption(t("compare.caption"))

# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

_METRIC_COLS_ORDER = [
    "sharpe", "RMSE", "MAE", "R2",
    "dir_acc_pct", "mae_pct", "profit_factor",
]


def _discover_sessions(models_dir: Path) -> list[dict]:
    out: list[dict] = []
    for meta_path in sorted(models_dir.glob("*_session.json")):
        prefix = meta_path.stem.removesuffix("_session")
        cbm = models_dir / f"{prefix}_session.cbm"
        npz = models_dir / f"{prefix}_session_arrays.npz"
        if not (cbm.exists() and npz.exists()):
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "prefix":      prefix,
            "target_col":  payload.get("target_col") or "target_return_1",
            "metrics":     payload.get("metrics") or {},
            "best_params": payload.get("best_params") or {},
            "n_features":  len(payload.get("feature_cols") or []),
            "mtime":       meta_path.stat().st_mtime,
        })
    return out


_col_title, _col_refresh = st.columns([8, 1])
with _col_refresh:
    if st.button("🔄", help=t("common.refresh"), key="btn_refresh_sessions"):
        st.cache_data.clear()
        st.rerun()

sessions = _discover_sessions(MODELS_DIR)

# Roadmap #9 — friendly empty state instead of bare st.warning + st.stop()
if not sessions:
    st.info(t("compare.no_sessions"))
    if st.button(t("compare.go_model"), key="btn_go_model_empty"):
        st.switch_page("pages/model_page.py")
    st.stop()

# ---------------------------------------------------------------------------
# Multi-select
# ---------------------------------------------------------------------------

options = [s["prefix"] for s in sessions]
default_sel = options[:2] if len(options) >= 2 else options

selected = st.multiselect(
    t("compare.select"),
    options=options,
    default=default_sel,
    help=t("compare.select_help"),
)

if not selected:
    st.info(t("compare.select_prompt"))
    st.stop()

# ---------------------------------------------------------------------------
# Load selected sessions
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _load_arrays(prefix: str) -> dict | None:
    res = load_session_result(prefix)
    if res is None:
        return None
    return {
        "prefix":       res["prefix"],
        "metrics":      res["metrics"],
        "best_params":  res["best_params"],
        "feature_cols": res["feature_cols"],
        "y_test":       np.asarray(res["y_test"], dtype=float),
        "y_pred":       np.asarray(res["y_pred"], dtype=float),
        "ts_test":      res["ts_test"],
        "target_col":   res.get("target_col") or "target_return_1",
    }


loaded: list[dict] = []
for pref in selected:
    d = _load_arrays(pref)
    if d is None:
        st.warning(f"Failed to load session `{pref}` — skipped.")
        continue
    loaded.append(d)

if not loaded:
    st.error("None of the selected sessions could be loaded.")
    st.stop()

# ---------------------------------------------------------------------------
# Side-by-side metrics table
# ---------------------------------------------------------------------------

st.subheader(t("compare.metrics"))

_rows: list[dict] = []
for d in loaded:
    row: dict[str, object] = {"prefix": d["prefix"], "target": d["target_col"]}
    m = d["metrics"]
    for k in _METRIC_COLS_ORDER:
        if k in m:
            row[k] = float(m[k])
    row["n_features"] = len(d["feature_cols"])
    row["n_test"] = int(len(d["y_test"]))
    _rows.append(row)

metrics_df = pd.DataFrame(_rows)

_fmt = {
    "sharpe":        "{:.4f}",
    "RMSE":          "{:.6f}",
    "MAE":           "{:.6f}",
    "R2":            "{:.4f}",
    "dir_acc_pct":   "{:.2f}",
    "mae_pct":       "{:.4f}",
    "profit_factor": "{:.4f}",
}
_fmt = {k: v for k, v in _fmt.items() if k in metrics_df.columns}

_style = metrics_df.style.format(_fmt)
if "sharpe" in metrics_df.columns:
    _style = _style.highlight_max(subset=["sharpe"], color="#2e7d32", axis=0)
if "RMSE" in metrics_df.columns:
    _style = _style.highlight_min(subset=["RMSE"], color="#2e7d32", axis=0)
st.dataframe(_style, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Roadmap #10 — Apply params to training form
# ---------------------------------------------------------------------------

with st.expander(t("compare.params"), expanded=False):
    _apply_prefix = st.selectbox(
        t("compare.select"),
        options=[d["prefix"] for d in loaded],
        key="compare_apply_prefix",
    )
    _apply_entry = next((d for d in loaded if d["prefix"] == _apply_prefix), None)
    if _apply_entry:
        st.json(_apply_entry["best_params"])
        if st.button(t("compare.apply_params"), key="btn_compare_apply"):
            _bp = _apply_entry.get("best_params") or {}
            if _bp:
                try:
                    from backend.model.train import save_grid_params_config
                    _str_vals = {k: str(v) for k, v in _bp.items()}
                    save_grid_params_config(_str_vals, max_combos=1)
                    st.session_state.pop("_grid_df_storage", None)
                except Exception as _e:
                    st.warning(f"Could not save params: {_e}")
            st.toast(t("compare.params_applied", p=_apply_prefix), icon="✅")

# ---------------------------------------------------------------------------
# Overlay: Cumulative P&L
# ---------------------------------------------------------------------------

st.subheader(t("compare.pnl"))

_palette = list(_C.overlay)
fig_pnl = go.Figure()
for i, d in enumerate(loaded):
    color = _palette[i % len(_palette)]
    strategy = np.sign(d["y_pred"]) * d["y_test"]
    cum = np.cumsum(strategy)
    x = d["ts_test"] if d["ts_test"] is not None else np.arange(len(cum))
    fig_pnl.add_trace(go.Scatter(
        x=x, y=cum, mode="lines", name=d["prefix"],
        line={"color": color, "width": 1.4},
    ))
_bh_base = loaded[0]
_bh = np.cumsum(_bh_base["y_test"])
fig_pnl.add_trace(go.Scatter(
    x=_bh_base["ts_test"], y=_bh, mode="lines",
    name=f"Buy & Hold [{_bh_base['prefix']}]",
    line={"color": _C.buy_and_hold, "width": 1.0, "dash": "dash"},
    opacity=0.7,
))
fig_pnl.add_hline(y=0, line_color=_C.zero_line, line_dash="dot", line_width=0.6)
fig_pnl.update_layout(
    xaxis_title="Time", yaxis_title="Cumulative return",
    height=380, hovermode="x unified",
    legend={"orientation": "h", "y": 1.10},
    margin={"t": 60, "b": 40},
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig_pnl, use_container_width=True)

# ---------------------------------------------------------------------------
# Overlay: Actual vs Predicted
# ---------------------------------------------------------------------------

with st.expander(t("compare.actual_pred"), expanded=False):
    _max_points = int(st.number_input(
        "Max points per session",
        min_value=200, max_value=50_000, value=3_000, step=500,
        key="_cmp_max_points",
    ))
    fig_ap = go.Figure()
    for i, d in enumerate(loaded):
        color = _palette[i % len(_palette)]
        n = len(d["y_test"])
        step = max(1, n // _max_points)
        fig_ap.add_trace(go.Scatter(
            x=d["ts_test"][::step], y=d["y_pred"][::step],
            mode="lines", name=f"pred [{d['prefix']}]",
            line={"color": color, "width": 1.0}, opacity=0.85,
        ))
    _ba = loaded[0]
    _step = max(1, len(_ba["y_test"]) // _max_points)
    fig_ap.add_trace(go.Scatter(
        x=_ba["ts_test"][::_step], y=_ba["y_test"][::_step],
        mode="lines", name=f"actual [{_ba['prefix']}]",
        line={"color": "black", "width": 1.0, "dash": "dot"}, opacity=0.65,
    ))
    fig_ap.add_hline(y=0, line_color=_C.zero_line, line_dash="dot", line_width=0.6)
    fig_ap.update_layout(
        xaxis_title="Time", yaxis_title="value",
        height=360, hovermode="x unified",
        legend={"orientation": "h", "y": 1.10},
        margin={"t": 60, "b": 40},
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_ap, use_container_width=True)

st.divider()
st.caption(
    f"Source: {MODELS_DIR}  ·  "
    f"Sessions found: {len(sessions)}  ·  "
    f"Selected: {len(loaded)}"
)
