"""Главная страница ModelLine."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = Path(__file__).resolve().parent
for _p in (ROOT, FRONTEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from services.i18n import t
from services.ui_components import render_lang_toggle
from services.version import get_version as _get_version

st.set_page_config(page_title="ModelLine", layout="wide", initial_sidebar_state="collapsed")

# Header row: title area | version badge | language toggle
_hcols = st.columns([7, 1.5, 1])
_ver = _get_version()
with _hcols[1]:
    st.caption(_ver["display"])
with _hcols[2]:
    render_lang_toggle(key="app_lang")

st.title(t("app.title"))
st.caption(t("app.caption"))

st.divider()

if st.button(t("app.btn_download"), use_container_width=True):
    st.switch_page("pages/download_page.py")

if st.button(t("app.btn_model"), use_container_width=True):
    st.switch_page("pages/model_page.py")

if st.button(t("app.btn_compare"), use_container_width=True):
    st.switch_page("pages/compare_page.py")

st.button(t("app.btn_backtest"), use_container_width=True, disabled=True)

st.divider()

# ── System status (roadmap #15) ───────────────────────────────────────────
st.subheader(t("app.system_status"))


@st.cache_data(ttl=30, show_spinner=False)
def _system_status() -> dict:
    result: dict = {"db_ok": False, "tables": 0, "models": 0, "store": "—"}
    try:
        from services.db_auth import load_db_config, load_local_config
        from services.store import store
        from backend.db import get_connection
        result["store"] = store.backend_name
        cfg = load_db_config(load_local_config())
        # use_pool=False — для health-check не стоит держать пул на локальный конфиг
        with get_connection(cfg, use_pool=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
                result["tables"] = int(cur.fetchone()[0])
        result["db_ok"] = True
    except Exception:
        pass
    try:
        import json
        from backend.model.config import MODELS_DIR
        reg = MODELS_DIR / "registry.json"
        if reg.exists():
            result["models"] = len(json.loads(reg.read_text(encoding="utf-8")))
    except Exception:
        pass
    return result


_st = _system_status()
_sc = st.columns(4)
_sc[0].metric(
    t("app.db_connected") if _st["db_ok"] else t("app.db_offline"),
    "✅" if _st["db_ok"] else "❌",
)
_sc[1].metric(t("app.tables_count"),  _st["tables"])
_sc[2].metric(t("app.models_count"),  _st["models"])
_sc[3].metric(t("app.store_backend"), _st["store"].split("(")[0].strip())
