"""Универсальное key-value хранилище для настроек приложения.

Приоритет бэкендов:
  1. Redis  — если доступен (REDIS_URL в env, по умолчанию redis://localhost:6379/0)
  2. SQLite — иначе (файл .app_store.db рядом с frontend/)

Публичный API (синглтон ``store``):
    store.get(key)              → str | None
    store.set(key, value, *, ex=None)   ex — TTL в секундах (только Redis)
    store.delete(key)
    store.get_json(key)         → dict | list | None
    store.set_json(key, obj)
    store.keys(pattern="*")     → list[str]

Ключи разбиты по пространствам имён через «:»:
    "db:"         → параметры подключения PostgreSQL
    "grid:"       → параметры Grid Search
    "ui:"         → UI-пользовательские настройки (symbol, timeframe, даты и т.д.)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# Папка, где лежит этот файл → два уровня вверх → корень воркспейса
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_SQLITE_PATH = _WORKSPACE_ROOT / ".app_store.db"
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    import redis as _redis_pkg
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class _SqliteStore:
    """Простой SQLite KV-store с thread-safe доступом."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at REAL NOT NULL DEFAULT (unixepoch('now'))
                )
            """)
            conn.commit()

    # --- public interface ---

    def get(self, key: str) -> str | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?,?,unixepoch('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            conn.commit()

    def keys(self, pattern: str = "*") -> list[str]:
        # Convert Redis-style glob to SQL LIKE
        like = pattern.replace("*", "%").replace("?", "_")
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT key FROM kv WHERE key LIKE ?", (like,)).fetchall()
        return [r[0] for r in rows]

    def backend_name(self) -> str:
        return f"SQLite ({self._path.name})"


# ---------------------------------------------------------------------------
# Redis backend wrapper
# ---------------------------------------------------------------------------

class _RedisStore:
    def __init__(self, url: str) -> None:
        self._client = _redis_pkg.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
        self._client.ping()  # raises ConnectionError if unavailable

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._client.set(key, value, ex=ex)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def keys(self, pattern: str = "*") -> list[str]:
        return [k for k in self._client.keys(pattern)]

    def backend_name(self) -> str:
        return f"Redis ({_REDIS_URL})"


# ---------------------------------------------------------------------------
# High-level AppStore facade
# ---------------------------------------------------------------------------

class AppStore:
    """Единый фасад: автоматически выбирает Redis или SQLite."""

    def __init__(self) -> None:
        self._backend: _RedisStore | _SqliteStore
        if _HAS_REDIS:
            try:
                self._backend = _RedisStore(_REDIS_URL)
                _LOG.info("[store] бэкенд: %s", self._backend.backend_name())
            except Exception as exc:
                _LOG.warning("[store] Redis недоступен (%s) — fallback на SQLite", exc)
                self._backend = _SqliteStore(_SQLITE_PATH)
        else:
            _LOG.info("[store] redis не установлен — используется SQLite")
            self._backend = _SqliteStore(_SQLITE_PATH)

    # --- raw ---

    def get(self, key: str) -> str | None:
        try:
            return self._backend.get(key)
        except Exception:
            return None

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        try:
            self._backend.set(key, value, ex=ex)
        except Exception as exc:
            _LOG.warning("[store] set(%s) ошибка: %s", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._backend.delete(key)
        except Exception:
            pass

    def keys(self, pattern: str = "*") -> list[str]:
        try:
            return self._backend.keys(pattern)
        except Exception:
            return []

    # --- JSON helpers ---

    def get_json(self, key: str) -> Any:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def set_json(self, key: str, obj: Any, *, ex: int | None = None) -> None:
        self.set(key, json.dumps(obj, ensure_ascii=False), ex=ex)

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

store = AppStore()

__all__ = ["store", "AppStore"]
