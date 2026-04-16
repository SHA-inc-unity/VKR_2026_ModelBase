"""Вспомогательные функции для хранения конфигурации PostgreSQL в локальном JSON-файле."""
from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG_FILE = Path(__file__).resolve().parents[2] / ".db_config.json"


def load_local_config() -> dict:
    """Загружает локальную конфигурацию подключения к БД из JSON-файла."""
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_local_config(config: dict) -> None:
    """Сохраняет параметры подключения в локальный JSON-файл."""
    _CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def clear_local_config() -> None:
    """Удаляет локальный файл конфигурации подключения."""
    if _CONFIG_FILE.exists():
        _CONFIG_FILE.unlink()


_GRID_PARAMS_FILE = Path(__file__).resolve().parents[2] / ".grid_params_config.json"


def load_grid_params_config() -> dict | None:
    """Загружает сохранённые параметры Grid Search из JSON-файла.

    Возвращает словарь с ключами 'param_values' (dict[str, str]) и
    'max_combos' (int), или None если файл отсутствует.
    """
    if _GRID_PARAMS_FILE.exists():
        try:
            return json.loads(_GRID_PARAMS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_grid_params_config(param_values_str: dict[str, str], max_combos: int) -> None:
    """Сохраняет текущие параметры Grid Search в локальный JSON-файл.

    param_values_str — словарь {param: 'v1, v2, ...'} (строковые значения из редактора).
    """
    payload = {
        "param_values": param_values_str,
        "max_combos":   int(max_combos),
    }
    _GRID_PARAMS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_grid_params_config() -> None:
    """Удаляет сохранённый файл параметров Grid Search."""
    if _GRID_PARAMS_FILE.exists():
        _GRID_PARAMS_FILE.unlink()


def load_db_config(overrides: dict | None = None) -> dict:
    """Читает конфигурацию PostgreSQL из окружения и необязательных override.

    Приоритет: override → переменные окружения → жёсткие умолчания.
    """
    config = {
        "host": os.getenv("PGHOST", "localhost"),
        "port": os.getenv("PGPORT", "5432"),
        "database": os.getenv("PGDATABASE", "crypt_date"),
        "user": os.getenv("PGUSER", ""),
        "password": os.getenv("PGPASSWORD", ""),
    }
    for key, value in dict(overrides or {}).items():
        if value is not None:
            config[key] = value
    config["host"] = str(config.get("host") or "localhost").strip() or "localhost"
    config["database"] = str(config.get("database") or "crypt_date").strip() or "crypt_date"
    config["user"] = str(config.get("user") or "").strip()
    config["password"] = str(config.get("password") or "")
    try:
        config["port"] = int(str(config.get("port") or "5432").strip())
    except (TypeError, ValueError):
        config["port"] = 5432
    return config
