from __future__ import annotations

import pandas as pd
import streamlit as st


def _render_df(df: pd.DataFrame, rows: int = 10) -> None:
    if df is None or df.empty:
        st.info("No data available.")
        return
    st.dataframe(df.tail(rows), use_container_width=True)


def render_market_table(df: pd.DataFrame, rows: int = 10) -> None:
    _render_df(df, rows)


def render_anomalies_table(df: pd.DataFrame, rows: int = 10) -> None:
    _render_df(df, rows)


def render_backtest_table(df: pd.DataFrame, rows: int = 10) -> None:
    _render_df(df, rows)
