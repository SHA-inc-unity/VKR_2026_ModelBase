"""Общие UI-компоненты ModelLine.

Содержит переиспользуемые блоки для всех страниц:
- render_lang_toggle()   — кнопка переключения языка RU/EN
- render_back_button()   — кнопка «← Назад» на app.py
- render_db_status()     — блок «Статус базы данных»
- render_db_settings()   — expander с настройками подключения

Импорт::

    from services.ui_components import (
        render_lang_toggle, render_back_button,
        render_db_status, render_db_settings,
    )
"""
from __future__ import annotations

import streamlit as st

from services.i18n import LANGS, get_lang, set_lang, t


# ---------------------------------------------------------------------------
# Language toggle
# ---------------------------------------------------------------------------

def render_lang_toggle(*, key: str = "lang_toggle_btn") -> None:
    """Кнопка переключения языка, выровненная по правому краю.

    Вызывать ДО первого st.title() на странице::

        render_lang_toggle()
        st.title(t("app.title"))
    """
    # Показываем метку противоположного языка — нажатие переключает на него
    label = t("common.lang_toggle")   # "EN" если текущий ru, "RU" если en
    if st.button(label, key=key, help="Switch language / Сменить язык"):
        current = get_lang()
        new_lang = "en" if current == "ru" else "ru"
        set_lang(new_lang)
        st.rerun()


# ---------------------------------------------------------------------------
# Back button
# ---------------------------------------------------------------------------

def render_back_button(target: str = "app.py") -> None:
    """Кнопка «← Назад» с переходом на target."""
    if st.button(t("common.back"), key=f"_back_{target}"):
        st.switch_page(target)


# ---------------------------------------------------------------------------
# DB status block (roadmap #4)
# ---------------------------------------------------------------------------

def render_db_status(db_config: dict, db_status: dict) -> None:
    """Блок метрик Host / Port / Database / Status.

    Args:
        db_config: словарь с ключами host, port, database.
        db_status: словарь с ключами connected (bool), message (str).
    """
    st.subheader(t("common.db_status"))
    cols = st.columns(4)
    cols[0].metric(t("common.host"),     db_config["host"])
    cols[1].metric(t("common.port"),     str(db_config["port"]))
    cols[2].metric(t("common.database"), db_config["database"])
    cols[3].metric(
        t("common.status"),
        t("common.connected") if db_status["connected"] else t("common.failed"),
    )
    if not db_status["connected"]:
        st.error(f"{t('common.db_error')}: {db_status['message']}")


# ---------------------------------------------------------------------------
# DB settings expander (roadmap #4)
# ---------------------------------------------------------------------------

def render_db_settings(
    restored_config: dict,
    *,
    save_key: str = "_db_save",
    clear_key: str = "_db_clear",
) -> dict:
    """Expander с полями подключения к PostgreSQL.

    Returns:
        dict с ключами host, port (str), database, user, password —
        значения из виджетов (ещё не прошедшие через load_db_config).
    """
    from services.db_auth import clear_local_config, load_db_config, save_local_config

    with st.expander(t("common.db_settings"), expanded=False):
        cols = st.columns(5)
        host     = cols[0].text_input(t("common.host"),     value=restored_config["host"],      key=f"{save_key}_host")
        port     = cols[1].text_input(t("common.port"),     value=str(restored_config["port"]), key=f"{save_key}_port")
        database = cols[2].text_input(t("common.database"), value=restored_config["database"],  key=f"{save_key}_db")
        user     = cols[3].text_input(t("common.user"),     value=restored_config["user"],      key=f"{save_key}_user")
        password = cols[4].text_input(
            t("common.password"), value=restored_config["password"],
            type="password", key=f"{save_key}_pass",
        )
        btn_cols = st.columns(2)
        if btn_cols[0].button(t("common.save_conn"), key=save_key):
            save_local_config(load_db_config(
                {"host": host, "port": port, "database": database,
                 "user": user, "password": password}
            ))
            st.toast(t("common.settings_saved"), icon="✅")
        if btn_cols[1].button(t("common.clear_conn"), key=clear_key):
            clear_local_config()
            st.rerun()

    return {"host": host, "port": port, "database": database,
            "user": user, "password": password}
