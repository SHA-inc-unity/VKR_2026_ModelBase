"""Единая точка доступа к PostgreSQL для всего проекта.

Решает 3 проблемы, найденных в инвентаризации:

1. **Дублирование конфигурации.** До рефакторинга 4 файла (scheduler, api/app,
   frontend/app, scripts/train_catboost) независимо читали одни и те же 5
   переменных окружения (PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD) с разными
   дефолтами. Теперь — ``load_db_config()`` / ``load_db_config_from_env()``.

2. **Нет пула соединений.** До рефакторинга каждый запрос (scheduler tick,
   REST /retrain, health-check, reload фронта) открывал новое TCP+SSL-соединение.
   Для PostgreSQL один handshake = 50–150 мс. Теперь — один процессный
   ``ThreadedConnectionPool`` с lazy-инициализацией, keep-alive коннекты.

3. **Размытая обработка ошибок.** Каждое место вручную писало
   ``conn = connect(); try: ... finally: conn.close()``. Теперь — единый
   контекст-менеджер ``get_connection()`` с гарантированным возвратом в пул и
   rollback при исключении.

Модуль не содержит бизнес-SQL (это по-прежнему `backend/dataset/database.py`
и `backend/model/loader.py`, принимающих ``psycopg2.extensions.connection``).
Такое разделение соответствует DIP: инфраструктура отделена от домена.

Потокобезопасность: ``ThreadedConnectionPool`` документирован как thread-safe;
контекст-менеджер ``get_connection()`` безопасен для одновременного вызова
из нескольких потоков (включая workers FastAPI / бэкграунд job-runner Streamlit).

Конфигурация через env:
    PGHOST       (default: localhost)
    PGPORT       (default: 5432)
    PGDATABASE   (default: crypt_date)
    PGUSER       (default: "")
    PGPASSWORD   (default: "")
    PG_POOL_MIN  (default: 1)   — min соединений в пуле
    PG_POOL_MAX  (default: 10)  — max соединений в пуле
    PG_CONNECT_TIMEOUT (default: 5)  — секунды на установку соединения
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
from psycopg2 import pool as pg_pool

_LOG = logging.getLogger(__name__)

# ── Конфигурация ─────────────────────────────────────────────────────────────

DEFAULT_DB_CONFIG: dict[str, Any] = {
    "host":     "localhost",
    "port":     5432,
    "database": "crypt_date",
    "user":     "",
    "password": "",
}


def load_db_config_from_env() -> dict[str, Any]:
    """Читает конфигурацию PostgreSQL из переменных окружения.

    Имена ключей (host/port/database/user/password) совпадают с
    ``frontend/services/db_auth.load_db_config``, что позволяет использовать
    результат и на бэке, и во фронте.
    """
    return {
        "host":     os.getenv("PGHOST",     DEFAULT_DB_CONFIG["host"]),
        "port":     int(os.getenv("PGPORT", str(DEFAULT_DB_CONFIG["port"]))),
        "database": os.getenv("PGDATABASE", DEFAULT_DB_CONFIG["database"]),
        "user":     os.getenv("PGUSER",     DEFAULT_DB_CONFIG["user"]),
        "password": os.getenv("PGPASSWORD", DEFAULT_DB_CONFIG["password"]),
    }


def config_to_psycopg2_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """Превращает внутренний формат конфига в kwargs для psycopg2.connect.

    psycopg2 принимает ``dbname`` (не ``database``) и не принимает пустые
    ``user``/``password``. Также добавляется ``connect_timeout`` из env.
    """
    kwargs: dict[str, Any] = {
        "host":    config["host"],
        "port":    int(config["port"]),
        "dbname":  config.get("database") or config.get("dbname") or DEFAULT_DB_CONFIG["database"],
        "connect_timeout": int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    }
    if config.get("user"):
        kwargs["user"] = config["user"]
    if config.get("password"):
        kwargs["password"] = config["password"]
    return kwargs


# ── Пул соединений (синглтон на процесс) ─────────────────────────────────────

_POOL: pg_pool.ThreadedConnectionPool | None = None
_POOL_SIG: tuple | None = None
_POOL_LOCK = threading.Lock()


def _pool_signature(config: dict[str, Any]) -> tuple:
    """Сигнатура пула — если параметры подключения изменились, пересоздаём."""
    return (
        config.get("host"),
        int(config.get("port", 0)),
        config.get("database") or config.get("dbname"),
        config.get("user", ""),
        config.get("password", ""),
    )


def get_pool(config: dict[str, Any] | None = None) -> pg_pool.ThreadedConnectionPool:
    """Возвращает процессный ThreadedConnectionPool.

    Lazy-инициализация: создаётся при первом вызове. Если config изменился
    (например, пользователь переподключился из UI к другой БД) — пул
    пересоздаётся с новыми параметрами, старый закрывается.
    """
    global _POOL, _POOL_SIG
    cfg = config if config is not None else load_db_config_from_env()
    sig = _pool_signature(cfg)

    with _POOL_LOCK:
        if _POOL is not None and _POOL_SIG == sig:
            return _POOL
        if _POOL is not None:
            try:
                _POOL.closeall()
            except Exception:  # noqa: BLE001
                _LOG.warning("closeall() on old pool failed", exc_info=True)
        minconn = int(os.getenv("PG_POOL_MIN", "1"))
        maxconn = int(os.getenv("PG_POOL_MAX", "10"))
        _POOL = pg_pool.ThreadedConnectionPool(
            minconn=minconn,
            maxconn=maxconn,
            **config_to_psycopg2_kwargs(cfg),
        )
        _POOL_SIG = sig
        _LOG.info(
            "postgres pool initialised: host=%s port=%s db=%s pool=%d..%d",
            cfg["host"], cfg["port"], cfg.get("database") or cfg.get("dbname"),
            minconn, maxconn,
        )
        return _POOL


def close_pool() -> None:
    """Закрывает пул (для graceful shutdown / тестов)."""
    global _POOL, _POOL_SIG
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.closeall()
            finally:
                _POOL = None
                _POOL_SIG = None


# ── Контекст-менеджер соединения ─────────────────────────────────────────────

@contextmanager
def get_connection(
    config: dict[str, Any] | None = None,
    *,
    use_pool: bool = True,
) -> Iterator[psycopg2.extensions.connection]:
    """Контекст-менеджер для работы с соединением PostgreSQL.

    При исключении откатывает транзакцию, при нормальном выходе — коммитит.
    В любом случае возвращает соединение в пул (или закрывает, если
    ``use_pool=False``).

    Пример::

        from backend.db import get_connection
        from backend.dataset.database import fetch_db_rows

        with get_connection() as conn:
            rows = fetch_db_rows(conn, "btcusdt_5m", start_ms, end_ms)

    Параметры:
        config: явная конфигурация. По умолчанию — из env.
        use_pool: если False — создаёт прямое соединение без пула
                  (для одноразовых скриптов/тестов).
    """
    if use_pool:
        pool = get_pool(config)
        conn = pool.getconn()
    else:
        cfg = config if config is not None else load_db_config_from_env()
        conn = psycopg2.connect(**config_to_psycopg2_kwargs(cfg))

    try:
        yield conn
        # Автокоммит незавершённой транзакции при нормальном выходе.
        # Если транзакций не было, connection.commit() — no-op.
        if not conn.closed:
            try:
                conn.commit()
            except psycopg2.Error:
                # Некоторые вызовы внутри уже сделали commit/rollback — игнорируем
                pass
    except Exception:
        if not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                _LOG.warning("rollback failed", exc_info=True)
        raise
    finally:
        if use_pool:
            try:
                get_pool(config).putconn(conn)
            except Exception:  # noqa: BLE001
                _LOG.warning("putconn failed, closing directly", exc_info=True)
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        else:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "DEFAULT_DB_CONFIG",
    "load_db_config_from_env",
    "config_to_psycopg2_kwargs",
    "get_pool",
    "close_pool",
    "get_connection",
]
