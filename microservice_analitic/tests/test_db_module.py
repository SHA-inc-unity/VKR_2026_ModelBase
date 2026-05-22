"""Тесты для backend.db: конфиг, pool, контекст-менеджер.

Настоящий PostgreSQL не используется — только проверка pure-функций и
поведения ``get_connection`` через подмену ``psycopg2.connect``.
"""
from __future__ import annotations

import pytest

from backend import db as db_module
from backend.db import (
    DEFAULT_DB_CONFIG,
    close_pool,
    config_to_psycopg2_kwargs,
    get_connection,
    load_db_config_from_env,
)


@pytest.fixture(autouse=True)
def _reset_pool():
    """Гарантируем чистое состояние пула между тестами."""
    close_pool()
    yield
    close_pool()


# ── load_db_config_from_env ──────────────────────────────────────────────────

def test_load_db_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_db_config_from_env()
    assert cfg["host"] == DEFAULT_DB_CONFIG["host"]
    assert cfg["port"] == DEFAULT_DB_CONFIG["port"]
    assert cfg["database"] == DEFAULT_DB_CONFIG["database"]


def test_load_db_config_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.example.com")
    monkeypatch.setenv("PGPORT", "6543")
    monkeypatch.setenv("PGDATABASE", "mydb")
    monkeypatch.setenv("PGUSER", "alice")
    monkeypatch.setenv("PGPASSWORD", "s3cret")
    cfg = load_db_config_from_env()
    assert cfg == {
        "host":     "db.example.com",
        "port":     6543,
        "database": "mydb",
        "user":     "alice",
        "password": "s3cret",
    }


# ── config_to_psycopg2_kwargs ────────────────────────────────────────────────

def test_config_to_psycopg2_kwargs_translates_database_to_dbname() -> None:
    cfg = {
        "host": "h", "port": 5432,
        "database": "mydb", "user": "u", "password": "p",
    }
    kw = config_to_psycopg2_kwargs(cfg)
    assert kw["dbname"] == "mydb"
    assert "database" not in kw
    assert kw["user"] == "u"
    assert kw["password"] == "p"
    assert "connect_timeout" in kw


def test_config_to_psycopg2_kwargs_drops_empty_credentials() -> None:
    cfg = {
        "host": "h", "port": 5432,
        "database": "d", "user": "", "password": "",
    }
    kw = config_to_psycopg2_kwargs(cfg)
    assert "user" not in kw
    assert "password" not in kw


def test_config_to_psycopg2_kwargs_accepts_legacy_dbname_key() -> None:
    cfg = {"host": "h", "port": 5432, "dbname": "legacy"}
    kw = config_to_psycopg2_kwargs(cfg)
    assert kw["dbname"] == "legacy"


# ── get_connection (direct, use_pool=False) через mock psycopg2.connect ──────

class _FakeConn:
    def __init__(self) -> None:
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_get_connection_commits_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeConn()
    monkeypatch.setattr(db_module.psycopg2, "connect", lambda **kw: fake)
    with get_connection({"host": "h", "port": 5432, "database": "d"}, use_pool=False) as conn:
        assert conn is fake
    assert fake.commits == 1
    assert fake.rollbacks == 0
    assert fake.closed is True


def test_get_connection_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConn()
    monkeypatch.setattr(db_module.psycopg2, "connect", lambda **kw: fake)
    with pytest.raises(RuntimeError, match="boom"):
        with get_connection(
            {"host": "h", "port": 5432, "database": "d"}, use_pool=False
        ) as _:
            raise RuntimeError("boom")
    assert fake.rollbacks == 1
    assert fake.commits == 0
    assert fake.closed is True


def test_get_connection_closes_even_if_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg2 as _pg

    class _BadCommitConn(_FakeConn):
        def commit(self) -> None:
            raise _pg.Error("in aborted transaction")

    fake = _BadCommitConn()
    monkeypatch.setattr(db_module.psycopg2, "connect", lambda **kw: fake)
    with get_connection({"host": "h", "port": 5432, "database": "d"}, use_pool=False):
        pass
    assert fake.closed is True


# ── get_pool (mocked) ────────────────────────────────────────────────────────

class _FakePool:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = kwargs
        self.closed = False

    def closeall(self) -> None:
        self.closed = True


def test_get_pool_singleton_and_reconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakePool] = []

    def factory(**kwargs):  # noqa: ANN003
        p = _FakePool(**kwargs)
        created.append(p)
        return p

    monkeypatch.setattr(db_module.pg_pool, "ThreadedConnectionPool", factory)

    cfg_a = {"host": "a", "port": 5432, "database": "d"}
    cfg_b = {"host": "b", "port": 5432, "database": "d"}

    p1 = db_module.get_pool(cfg_a)
    p2 = db_module.get_pool(cfg_a)
    assert p1 is p2  # singleton для того же конфига

    p3 = db_module.get_pool(cfg_b)
    assert p3 is not p1  # другой конфиг → пересоздание
    assert created[0].closed is True  # старый пул закрыт
