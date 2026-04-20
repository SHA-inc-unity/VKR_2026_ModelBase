"""Market data chart builders — Plotly-based, Streamlit-ready.

Architecture
------------
- ``FIELD_META`` — centralised metadata per field (label, colour, group).
- ``CHART_GROUPS`` — ordered list of logical groups; each group knows which
  fields it owns and any rendering hints (RSI range, dual-axis, etc.).
- Dedicated builder per group (``build_*``) returns a ``go.Figure | None``.
- ``render_charts()`` is the single public entry-point called from the page.

Extending
---------
* New indicator (Bollinger, MACD): add its fields to ``FIELD_META`` + a new
  entry in ``CHART_GROUPS``, then write a ``build_*`` function and wire it
  into ``render_charts``.
* Model signals / anomaly highlighting: pass an extra ``annotations`` kwarg
  to ``render_charts`` and call ``fig.add_vline`` / ``fig.add_shape`` there.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pandas.api.types import is_numeric_dtype
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

# Each entry: label shown in legend/axis, hex colour
FIELD_META: dict[str, dict[str, str]] = {
    "index_price":  {"label": "Index Price",   "color": "#2196F3"},
    "funding_rate": {"label": "Funding Rate",  "color": "#E91E63"},
    "open_interest":{"label": "Open Interest", "color": "#AB47BC"},
    "rsi":          {"label": "RSI",           "color": "#FFA726"},
}

FALLBACK_COLORS: list[str] = [
    "#26A69A",
    "#42A5F5",
    "#7E57C2",
    "#EC407A",
    "#FF7043",
    "#9CCC65",
    "#29B6F6",
    "#FFCA28",
    "#8D6E63",
    "#66BB6A",
]
PLOT_FIELD_EXCLUDE = {"timestamp_utc", "symbol", "exchange", "timeframe"}

# Ordered list — defines multiselect order and group membership
CHART_GROUPS: list[dict[str, Any]] = [
    {
        "key":    "price",
        "label":  "Price",
        "fields": ["index_price"],
        "y_title": "Price (USD)",
    },
    {
        "key":    "indicators",
        "label":  "Indicators",
        "fields": ["rsi"],
        "y_title": "RSI",
        "y_range": [0, 100],
        "hlines": [
            {"y": 30, "color": "#66BB6A", "dash": "dash", "label": "Oversold (30)"},
            {"y": 70, "color": "#EF5350", "dash": "dash", "label": "Overbought (70)"},
        ],
    },
    {
        "key":    "market_metrics",
        "label":  "Market Metrics",
        "fields": ["funding_rate", "open_interest"],
        "y_title": "Funding Rate",
        # open_interest uses a secondary right-hand Y axis
        "dual_axis_field": "open_interest",
        "y2_title": "Open Interest",
    },
]

# Flat ordered list of all plottable fields (used for multiselect)
ALL_PLOT_FIELDS: list[str] = [f for g in CHART_GROUPS for f in g["fields"]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prepare(df: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    """Return a sorted, NaN-filtered frame containing only *available* fields."""
    available = [f for f in fields if f in df.columns]
    if not available:
        return pd.DataFrame()
    frame = df[["timestamp_utc", *available]].copy()
    frame = frame.sort_values("timestamp_utc").reset_index(drop=True)
    frame = frame.dropna(subset=available, how="all")
    return frame


def _field_style(field: str) -> dict[str, str]:
    meta = FIELD_META.get(field)
    if meta is not None:
        return meta
    color = FALLBACK_COLORS[sum(ord(ch) for ch in field) % len(FALLBACK_COLORS)]
    return {"label": field.replace("_", " ").title(), "color": color}


def get_plot_fields(df: pd.DataFrame) -> list[str]:
    """Возвращает все числовые метрики, доступные для графиков."""
    ordered_core = [
        field for field in ALL_PLOT_FIELDS
        if field in df.columns and is_numeric_dtype(df[field])
    ]
    dynamic_fields = [
        column_name
        for column_name in df.columns
        if column_name not in PLOT_FIELD_EXCLUDE
        and column_name not in ordered_core
        and is_numeric_dtype(df[column_name])
    ]
    return ordered_core + dynamic_fields


def _base_layout(title: str, y_title: str) -> dict[str, Any]:
    return dict(
        title=dict(text=title, font=dict(size=15)),
        xaxis=dict(title="Time (UTC)", showgrid=True, gridcolor="#2a2a2a"),
        yaxis=dict(title=y_title, showgrid=True, gridcolor="#2a2a2a"),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        margin=dict(l=50, r=50, t=70, b=50),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(14,17,23,1)",
    )


def _line(x, y, name: str, color: str, yaxis: str = "y") -> go.Scatter:
    return go.Scatter(
        x=x,
        y=y,
        name=name,
        mode="lines",
        line=dict(color=color, width=1.5),
        yaxis=yaxis,
        connectgaps=False,
    )


# ---------------------------------------------------------------------------
# Group-specific builders
# ---------------------------------------------------------------------------

def build_price_chart(df: pd.DataFrame, fields: list[str] | None = None) -> go.Figure | None:
    frame = _prepare(df, fields or ["index_price"])
    if frame.empty:
        return None
    fig = go.Figure(layout=_base_layout("Price", "Price (USD)"))
    meta = _field_style("index_price")
    fig.add_trace(_line(frame["timestamp_utc"], frame["index_price"],
                        meta["label"], meta["color"]))
    return fig


def build_rsi_chart(df: pd.DataFrame, fields: list[str] | None = None) -> go.Figure | None:
    frame = _prepare(df, fields or ["rsi"])
    if frame.empty:
        return None
    fig = go.Figure(layout=_base_layout("RSI", "RSI"))
    meta = _field_style("rsi")
    fig.add_trace(_line(frame["timestamp_utc"], frame["rsi"],
                        meta["label"], meta["color"]))
    fig.update_yaxes(range=[0, 100])
    for level, color, label in [
        (30, "#66BB6A", "Oversold (30)"),
        (70, "#EF5350", "Overbought (70)"),
    ]:
        fig.add_hline(
            y=level,
            line_dash="dash",
            line_color=color,
            annotation_text=label,
            annotation_position="top right",
            annotation_font_size=11,
        )
    return fig


def build_market_metrics_chart(df: pd.DataFrame, fields: list[str] | None = None) -> go.Figure | None:
    active_fields = fields or ["funding_rate", "open_interest"]
    frame_fr = _prepare(df, ["funding_rate"] if "funding_rate" in active_fields else [])
    frame_oi = _prepare(df, ["open_interest"] if "open_interest" in active_fields else [])
    if frame_fr.empty and frame_oi.empty:
        return None

    layout = _base_layout("Market Metrics", "Funding Rate")
    if not frame_oi.empty:
        layout["yaxis2"] = dict(
            title="Open Interest",
            overlaying="y",
            side="right",
            showgrid=False,
        )

    fig = go.Figure(layout=layout)
    if not frame_fr.empty:
        m = _field_style("funding_rate")
        fig.add_trace(_line(frame_fr["timestamp_utc"], frame_fr["funding_rate"],
                            m["label"], m["color"], yaxis="y"))
    if not frame_oi.empty:
        m = _field_style("open_interest")
        fig.add_trace(_line(frame_oi["timestamp_utc"], frame_oi["open_interest"],
                            m["label"], m["color"], yaxis="y2"))
    return fig


def build_generic_metric_chart(df: pd.DataFrame, field: str) -> go.Figure | None:
    frame = _prepare(df, [field])
    if frame.empty:
        return None
    meta = _field_style(field)
    fig = go.Figure(layout=_base_layout(meta["label"], meta["label"]))
    fig.add_trace(_line(frame["timestamp_utc"], frame[field], meta["label"], meta["color"]))
    return fig


def build_orderbook_chart(df: pd.DataFrame) -> go.Figure | None:
    """Optional: L1 orderbook snapshot chart — NOT part of the backtest dataset.

    Bybit v5 public API does not expose historical L1 quotes, so these fields
    are never populated in the main pipeline.  This builder is kept for future
    use (e.g. if a separate real-time snapshot feed is added) but is NOT wired
    into CHART_GROUPS or _GROUP_BUILDER.
    """
    fields = ["bid1_price", "ask1_price", "bid1_size", "ask1_size"]
    frame = _prepare(df, fields)
    if frame.empty:
        return None
    fig = go.Figure(layout=_base_layout("Orderbook", "Price / Size"))
    for field in fields:
        if field not in frame.columns:
            continue
        m = FIELD_META[field]
        fig.add_trace(_line(frame["timestamp_utc"], frame[field],
                            m["label"], m["color"]))
    return fig


def build_overlay_chart(df: pd.DataFrame, fields: list[str]) -> go.Figure | None:
    available = [f for f in fields if f in df.columns]
    frame = _prepare(df, available)
    if frame.empty:
        return None
    fig = go.Figure(layout=_base_layout("Overlay — All Selected Metrics", "Value"))
    for field in available:
        if frame[field].dropna().empty:
            continue
        m = _field_style(field)
        fig.add_trace(_line(frame["timestamp_utc"], frame[field],
                            m["label"], m["color"]))
    return fig


# ---------------------------------------------------------------------------
# Streamlit render entry-point
# ---------------------------------------------------------------------------

_GROUP_BUILDER = {
    "price":          build_price_chart,
    "indicators":     build_rsi_chart,
    "market_metrics": build_market_metrics_chart,
    # "orderbook": build_orderbook_chart,  # L1 data not available from Bybit v5 history
}


def render_charts(
    df: pd.DataFrame,
    selected_fields: list[str],
    overlay: bool,
) -> None:
    """Render market-data charts into the active Streamlit page.

    Parameters
    ----------
    df:
        DataFrame that must contain ``timestamp_utc`` plus any subset of the
        fields declared in ``ALL_PLOT_FIELDS``.
    selected_fields:
        Subset of ``ALL_PLOT_FIELDS`` chosen by the user.
    overlay:
        If ``True`` all selected metrics share one figure; otherwise each
        logical group gets its own figure.
    """
    if df.empty or not selected_fields:
        st.info("Select at least one metric to display.")
        return

    if overlay:
        fig = build_overlay_chart(df, selected_fields)
        if fig is not None:
            st.plotly_chart(fig, width="stretch")
        return

    # Grouped mode — one figure per non-empty group
    rendered = 0
    rendered_fields: set[str] = set()
    for group in CHART_GROUPS:
        active = [f for f in group["fields"] if f in selected_fields]
        if not active:
            continue
        builder = _GROUP_BUILDER.get(group["key"])
        if builder is None:
            continue
        fig = builder(df, active)
        if fig is None:
            continue
        st.subheader(group["label"])
        st.plotly_chart(fig, width="stretch")
        rendered += 1
        rendered_fields.update(active)

    for field in selected_fields:
        if field in rendered_fields:
            continue
        fig = build_generic_metric_chart(df, field)
        if fig is None:
            continue
        st.subheader(_field_style(field)["label"])
        st.plotly_chart(fig, width="stretch")
        rendered += 1

    if rendered == 0:
        st.info("No data available for the selected metrics.")
