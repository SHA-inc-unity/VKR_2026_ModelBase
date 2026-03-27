from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _anomaly_fill(level: str) -> str:
    return {
        "warning": "rgba(255,193,7,0.18)",
        "stress": "rgba(255,152,0,0.18)",
        "shock": "rgba(244,67,54,0.20)",
    }.get(str(level), "rgba(120,120,120,0.12)")


def render_price_chart(df: pd.DataFrame, anomaly_windows: pd.DataFrame | None, rows_to_show: int = 1000, latest_prediction: dict | None = None) -> None:
    if df is None or df.empty:
        st.info("No market data loaded.")
        return

    view = df.tail(rows_to_show).copy()
    fig = go.Figure()
    if {"open", "high", "low", "close"}.issubset(view.columns):
        fig.add_trace(go.Candlestick(x=view["timestamp"], open=view["open"], high=view["high"], low=view["low"], close=view["close"], name="Price"))
    else:
        fig.add_trace(go.Scatter(x=view["timestamp"], y=view["close"], mode="lines", name="Close"))

    if "mark_close" in view.columns:
        fig.add_trace(go.Scatter(x=view["timestamp"], y=view["mark_close"], mode="lines", name="Mark", opacity=0.55))

    if anomaly_windows is not None and not anomaly_windows.empty:
        for _, win in anomaly_windows.iterrows():
            start_ts = win.get("start_ts")
            end_ts = win.get("end_ts")
            if pd.isna(start_ts) or pd.isna(end_ts):
                continue
            if end_ts < view["timestamp"].min() or start_ts > view["timestamp"].max():
                continue
            fig.add_vrect(x0=start_ts, x1=end_ts, fillcolor=_anomaly_fill(win.get("anomaly_level", "warning")), line_width=0, layer="below")

    if latest_prediction:
        last_ts = view["timestamp"].iloc[-1]
        forecast_ts = last_ts + (view["timestamp"].diff().median() if len(view) > 1 else pd.Timedelta(minutes=5)) * 36
        pred_price = latest_prediction.get("direct_pred_price") or latest_prediction.get("direct_pred")
        low = latest_prediction.get("range_pred_low")
        high = latest_prediction.get("range_pred_high")
        if pred_price is not None:
            fig.add_trace(go.Scatter(x=[last_ts, forecast_ts], y=[view["close"].iloc[-1], pred_price], mode="lines+markers", name="Direct Forecast", line=dict(dash="dash")))
        if low is not None and high is not None:
            fig.add_trace(go.Scatter(x=[forecast_ts, forecast_ts], y=[low, high], mode="lines", line=dict(width=10), name="Forecast Range"))

    fig.update_layout(height=520, margin=dict(l=10, r=10, t=20, b=10), xaxis_title=None, yaxis_title="Price", xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)


def render_backtest_chart(backtest_df: pd.DataFrame, rows_to_show: int = 500) -> None:
    if backtest_df is None or backtest_df.empty:
        st.info("No backtest results found yet.")
        return
    view = backtest_df.tail(rows_to_show).copy()
    x = view["timestamp"] if "timestamp" in view.columns else view.index
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=view["target_future_close"], mode="lines", name="Actual"))
    if "direct_pred_price" in view.columns:
        fig.add_trace(go.Scatter(x=x, y=view["direct_pred_price"], mode="lines", name="Direct Forecast"))
    if "baseline_persistence_price" in view.columns:
        fig.add_trace(go.Scatter(x=x, y=view["baseline_persistence_price"], mode="lines", name="Persistence", opacity=0.5))
    if {"range_pred_low", "range_pred_high"}.issubset(view.columns):
        fig.add_trace(go.Scatter(x=x, y=view["range_pred_high"], mode="lines", line=dict(width=0), showlegend=False))
        fig.add_trace(go.Scatter(x=x, y=view["range_pred_low"], mode="lines", fill="tonexty", name="Forecast Range", line=dict(width=0)))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), xaxis_title=None, yaxis_title="Price")
    st.plotly_chart(fig, use_container_width=True)


def render_confidence_chart(backtest_df: pd.DataFrame, rows_to_show: int = 500) -> None:
    if backtest_df is None or backtest_df.empty or "confidence" not in backtest_df.columns:
        st.info("Confidence history will appear here after backtest results are generated.")
        return
    view = backtest_df.tail(rows_to_show).copy()
    x = view["timestamp"] if "timestamp" in view.columns else view.index
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=view["confidence"], mode="lines", name="Confidence"))
    fig.add_hrect(y0=0.0, y1=0.5, fillcolor="rgba(244,67,54,0.12)", line_width=0)
    fig.add_hrect(y0=0.5, y1=0.75, fillcolor="rgba(255,193,7,0.12)", line_width=0)
    fig.add_hrect(y0=0.75, y1=1.0, fillcolor="rgba(76,175,80,0.12)", line_width=0)
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10), yaxis_range=[0, 1])
    st.plotly_chart(fig, use_container_width=True)


def render_confidence_gauge(confidence: float | None) -> None:
    if confidence is None:
        st.info("No confidence value available.")
        return
    val = float(confidence)
    if val <= 1.0:
        val *= 100
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number={"suffix": "%"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"thickness": 0.25},
            "steps": [
                {"range": [0, 50], "color": "rgba(244,67,54,0.30)"},
                {"range": [50, 75], "color": "rgba(255,193,7,0.30)"},
                {"range": [75, 100], "color": "rgba(76,175,80,0.30)"},
            ],
        },
        title={"text": "Confidence"},
    ))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)


def render_error_histogram(backtest_df: pd.DataFrame) -> None:
    if backtest_df is None or backtest_df.empty:
        st.info("No error distribution available.")
        return
    if {"target_future_close", "direct_pred_price"}.issubset(backtest_df.columns):
        errors = (backtest_df["direct_pred_price"] - backtest_df["target_future_close"]).dropna()
        fig = go.Figure(go.Histogram(x=errors, nbinsx=50, name="Forecast Error"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10), xaxis_title="Error", yaxis_title="Count")
        st.plotly_chart(fig, use_container_width=True)


def render_coverage_chart(backtest_df: pd.DataFrame, rows_to_show: int = 500) -> None:
    if backtest_df is None or backtest_df.empty or not {"target_future_close", "range_pred_low", "range_pred_high"}.issubset(backtest_df.columns):
        st.info("No range coverage series available.")
        return
    view = backtest_df.tail(rows_to_show).copy()
    covered = ((view["target_future_close"] >= view["range_pred_low"]) & (view["target_future_close"] <= view["range_pred_high"])).astype(int)
    fig = go.Figure(go.Scatter(x=view["timestamp"], y=covered, mode="lines", name="Covered"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=20, b=10), yaxis=dict(range=[-0.1, 1.1], tickvals=[0, 1]))
    st.plotly_chart(fig, use_container_width=True)


def render_feature_importance(importance: dict | None, key: str, top_n: int = 12) -> None:
    if not importance or key not in importance or not importance[key]:
        st.info("Feature importance not available.")
        return
    items = sorted(importance[key].items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    fig = go.Figure(go.Bar(x=[v for _, v in items][::-1], y=[k for k, _ in items][::-1], orientation="h"))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), xaxis_title="Importance")
    st.plotly_chart(fig, use_container_width=True)
