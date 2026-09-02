"""Test fixtures.

Tests run against a SEPARATE database (argus_test by default) on the same server
as DATABASE_URL, so they never touch demo data. The database is created if
missing, the schema is reset once per session, and every table is truncated
between tests.

If no Postgres is reachable, the whole suite is skipped with a clear message
(bring it up with `docker compose up -d`).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from argus import db

FIXTURES = Path(__file__).resolve().parent / "fixtures"

_base = os.environ.get("ARGUS_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or db.DEFAULT_DSN
_parts = conninfo_to_dict(_base)
_TEST_DB = os.environ.get("ARGUS_TEST_DB", "argus_test")
ADMIN_DSN = make_conninfo(**{**_parts, "dbname": "postgres"})
TEST_DSN = make_conninfo(**{**_parts, "dbname": _TEST_DB})
_DB_AVAILABLE = False
_DB_SKIP_REASON = ""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "no_db: test does not require the ARGUS Postgres fixture")


def _session_requires_db(request) -> bool:
    return any(item.get_closest_marker("no_db") is None
               for item in getattr(request.session, "items", ()))


def _ensure_database() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from pg_database where datname = %s", (_TEST_DB,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("create database {}").format(sql.Identifier(_TEST_DB)))


@pytest.fixture(scope="session", autouse=True)
def _db_setup(request):
    global _DB_AVAILABLE, _DB_SKIP_REASON
    if not _session_requires_db(request):
        _DB_SKIP_REASON = "No selected tests require the ARGUS Postgres fixture."
        yield
        return
    try:
        _ensure_database()
    except psycopg.OperationalError as exc:  # no server reachable
        _DB_SKIP_REASON = (f"Postgres not reachable at {ADMIN_DSN!r}: {exc}. "
                           "Run `docker compose up -d` first.")
        yield
        return
    # Point the action handlers at the test database. python-dotenv does not
    # override an already-set env var, so this wins over .env.
    os.environ["DATABASE_URL"] = TEST_DSN
    db.init_db(reset=True, dsn=TEST_DSN)
    _DB_AVAILABLE = True
    yield


@pytest.fixture(autouse=True)
def clean_db(request):
    """Truncate every table before each test for isolation."""
    if request.node.get_closest_marker("no_db") is not None:
        yield
        return
    if not _DB_AVAILABLE:
        pytest.skip(_DB_SKIP_REASON)
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("select tablename from pg_tables where schemaname = 'public'")
            tables = [r[0] for r in cur.fetchall()]
            if tables:
                cur.execute(
                    sql.SQL("truncate {} restart identity cascade").format(
                        sql.SQL(", ").join(sql.Identifier(t) for t in tables)
                    )
                )
    yield


@pytest.fixture
def db_conn():
    """A read connection (autocommit) for inspecting committed action results."""
    conn = psycopg.connect(TEST_DSN, row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
