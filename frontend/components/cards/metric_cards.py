from __future__ import annotations

from typing import Optional

import streamlit as st

from frontend.services.formatters import fmt_confidence, fmt_number, fmt_percent, fmt_price


def render_market_metrics(summary: dict) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Last Close", fmt_price(summary.get("last_close")))
    c2.metric("1h Change", fmt_percent(summary.get("change_1h"), scale_100=True))
    c3.metric("24h Change", fmt_percent(summary.get("change_24h"), scale_100=True))
    c4.metric("Last Volume", fmt_number(summary.get("last_volume"), digits=3))
    c5.metric("Volatility 1h", fmt_percent(summary.get("volatility_1h"), digits=3, scale_100=True))
    c6.metric("Rows Loaded", str(summary.get("rows", "—")))


def render_prediction_metrics(latest_prediction: Optional[dict], backtest_summary: Optional[dict] = None) -> None:
    if not latest_prediction:
        st.info("Prediction metrics will appear here after backtest results are generated.")
        return

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Current Close", fmt_price(latest_prediction.get("current_close") or latest_prediction.get("close")))
    c2.metric("Direct Forecast (+3h)", fmt_price(latest_prediction.get("direct_pred_price") or latest_prediction.get("direct_pred")))
    c3.metric("Predicted Return", fmt_percent(latest_prediction.get("direct_pred_return"), scale_100=True))
    c4.metric("Range Forecast", f"{fmt_price(latest_prediction.get('range_pred_low'))} – {fmt_price(latest_prediction.get('range_pred_high'))}")
    c5.metric("Confidence", fmt_confidence(latest_prediction.get("confidence")))
    c6.metric("Risk Level", _risk_label(latest_prediction.get("confidence"), latest_prediction.get("anomaly_level") or latest_prediction.get("anomaly_type")))

    if backtest_summary:
        direct = backtest_summary.get("direct_model", {})
        range_model = backtest_summary.get("range_model", {})
        baselines = backtest_summary.get("direct_baselines", {})
        c7, c8, c9, c10, c11 = st.columns(5)
        c7.metric("Direct MAE", fmt_number(direct.get("MAE")))
        c8.metric("Persistence MAE", fmt_number((baselines.get("persistence") or {}).get("MAE")))
        c9.metric("Sign Accuracy", fmt_percent(direct.get("sign_accuracy"), scale_100=True))
        c10.metric("Range Coverage", fmt_percent(range_model.get("coverage"), scale_100=True))
        c11.metric("Norm Band Width", fmt_percent(range_model.get("normalized_band_width"), scale_100=True))

        # Per-model sign accuracy (direction, range center)
        per_model = backtest_summary.get("per_model_sign_accuracy", {})
        direct_pm = per_model.get("direct", {})
        direction_pm = per_model.get("direction", {})
        range_pm = per_model.get("range", {})
        r1, r2, r3 = st.columns(3)
        r1.metric("Direct Sign", fmt_percent(direct_pm.get("sign") or direct.get("sign_accuracy"), scale_100=True))
        r2.metric("Direction Label", fmt_percent(direction_pm.get("label_accuracy"), scale_100=True))
        r3.metric("Range Center", fmt_percent(range_pm.get("center_sign_accuracy_label"), scale_100=True))


def _risk_label(confidence: Optional[float], anomaly_level: Optional[str]) -> str:
    level = str(anomaly_level or "normal").lower()
    if level in {"shock", "stress"}:
        return "High"
    if confidence is None:
        return "—"
    c = float(confidence)
    if c <= 1:
        c *= 100
    if c >= 75:
        return "Low"
    if c >= 50:
        return "Medium"
    return "High"
