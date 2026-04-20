"""Управление настройками подключения к БД и UI-предпочтениями через AppStore.

Данные хранятся в Redis (если доступен) или в SQLite (.app_store.db).
Ключи пространств имён:
    db:config          — параметры подключения PostgreSQL
    grid:params        — параметры Grid Search (param_values, max_combos)
    ui:prefs           — UI-настройки (symbol, timeframe, даты, target, CV и т.д.)
"""
from __future__ import annotations

import os
from typing import Any

from .store import store

# ---------------------------------------------------------------------------
# Namespace keys
# ---------------------------------------------------------------------------

_KEY_DB      = "db:config"
_KEY_GRID    = "grid:params"
_KEY_UI      = "ui:prefs"


# ---------------------------------------------------------------------------
# DB connection config
# ---------------------------------------------------------------------------

def load_db_config(overrides: dict | None = None) -> dict:
    """Читает конфигурацию PostgreSQL (store → env → defaults).

    Приоритет: overrides > store > env vars > жёсткие defaults.
    """
    saved = store.get_json(_KEY_DB) or {}
    config: dict[str, Any] = {
        "host":     os.getenv("PGHOST",     saved.get("host", "localhost")),
        "port":     os.getenv("PGPORT",     str(saved.get("port", 5432))),
        "database": os.getenv("PGDATABASE", saved.get("database", "crypt_date")),
        "user":     os.getenv("PGUSER",     saved.get("user", "")),
        "password": os.getenv("PGPASSWORD", saved.get("password", "")),
    }
    for key, value in dict(overrides or {}).items():
        if value is not None:
            config[key] = value
    config["host"]     = str(config.get("host") or "localhost").strip() or "localhost"
    config["database"] = str(config.get("database") or "crypt_date").strip() or "crypt_date"
    config["user"]     = str(config.get("user") or "").strip()
    config["password"] = str(config.get("password") or "")
    try:
        config["port"] = int(str(config.get("port") or "5432").strip())
    except (TypeError, ValueError):
        config["port"] = 5432
    return config


def save_local_config(config: dict) -> None:
    """Сохраняет параметры подключения в store."""
    store.set_json(_KEY_DB, {
        "host":     config.get("host", "localhost"),
        "port":     int(config.get("port", 5432)),
        "database": config.get("database", "crypt_date"),
        "user":     config.get("user", ""),
        "password": config.get("password", ""),
    })


def load_local_config() -> dict:
    """Alias: читает сохранённую конфигурацию из store (без env-слияния)."""
    return store.get_json(_KEY_DB) or {}


def clear_local_config() -> None:
    """Удаляет сохранённую конфигурацию подключения."""
    store.delete(_KEY_DB)


# ---------------------------------------------------------------------------
# Grid Search params config
# ---------------------------------------------------------------------------

_GRID_DEFAULTS: dict[str, Any] = {
    "param_values": {},
    "max_combos":   10,
}


def load_grid_params_config() -> dict | None:
    """Загружает параметры Grid Search из store.

    Возвращает dict с ключами ``param_values`` и ``max_combos``, или None.
    """
    return store.get_json(_KEY_GRID)


def save_grid_params_config(param_values_str: dict[str, str], max_combos: int) -> None:
    """Сохраняет параметры Grid Search в store."""
    store.set_json(_KEY_GRID, {
        "param_values": param_values_str,
        "max_combos":   int(max_combos),
    })


def clear_grid_params_config() -> None:
    """Удаляет сохранённые параметры Grid Search."""
    store.delete(_KEY_GRID)


# ---------------------------------------------------------------------------
# UI preferences — symbol, timeframe, dates, target, CV settings, etc.
# ---------------------------------------------------------------------------

_UI_DEFAULTS: dict[str, Any] = {
    # model_page
    "symbol":            "BTCUSDT",
    "timeframe":         "60m",
    "date_from":         None,
    "date_to":           None,
    "target_col":        "target_return_1",
    "cv_mode":           "expanding",
    "max_train_size":    0,
    "use_gpu":           False,
    "n_trials":          50,
    "use_disk_cache":    True,
    "use_mlflow":        False,
    "mlflow_uri":        "http://localhost:5000",
    "mlflow_experiment": "ModelLine",
    # download_page
    "ds_symbol":         "BTCUSDT",
    "ds_timeframe":      "60m",
    "ds_date_from":      "2024-01-01",
    "ds_date_to":        None,
}


def load_ui_prefs() -> dict[str, Any]:
    """Загружает UI-предпочтения из store (с fallback на defaults)."""
    saved = store.get_json(_KEY_UI) or {}
    return {**_UI_DEFAULTS, **saved}


def save_ui_prefs(prefs: dict[str, Any]) -> None:
    """Сохраняет UI-предпочтения в store.

    Принимает любое подмножество ключей из _UI_DEFAULTS — неизвестные ключи
    игнорируются; значения сливаются с уже сохранёнными.
    """
    existing = load_ui_prefs()
    allowed  = set(_UI_DEFAULTS)
    merged   = {**existing, **{k: v for k, v in prefs.items() if k in allowed}}
    store.set_json(_KEY_UI, merged)


def clear_ui_prefs() -> None:
    """Удаляет сохранённые UI-предпочтения."""
    store.delete(_KEY_UI)
