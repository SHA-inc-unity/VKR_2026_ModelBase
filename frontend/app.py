"""Главный экран технического демо Bybit Dataset."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="Dataset Demo", layout="wide", initial_sidebar_state="collapsed")

st.title("Bybit Dataset Demo")
st.caption("Технический демо — покрытие данных, загрузка пропусков, PostgreSQL, графики.")

st.divider()

if st.button("Open dataset window", use_container_width=True):
    st.switch_page("pages/download_page.py")

st.button("Models (coming soon)", use_container_width=True, disabled=True)
st.button("Backtest (coming soon)", use_container_width=True, disabled=True)

