"""SQLDatabase (Postgres via le core Rust).

La résolution de DSN se teste sans base ; le reste demande un Postgres :

    docker run -d --name vignemale-pg -p 5433:5432 -e POSTGRES_PASSWORD=vignemale postgres:16
    VIGNEMALE_TEST_PG=postgres://postgres:vignemale@127.0.0.1:5433/postgres python -m pytest tests/ -k sqldb
"""

import os

import pytest

from vignemale import SQLDatabase, SQLError

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="pose VIGNEMALE_TEST_PG (DSN Postgres) pour l'activer"
)


def test_dsn_resolution_specific_wins(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_MABASE", "postgres://specifique")
    monkeypatch.setenv("VIGNEMALE_SQLDB", "postgres://commun")
    assert SQLDatabase("mabase").dsn == "postgres://specifique"
    assert SQLDatabase("autre").dsn == "postgres://commun"


def test_dsn_missing_is_clear_error(monkeypatch):
    monkeypatch.delenv("VIGNEMALE_SQLDB_SANSDSN", raising=False)
    monkeypatch.delenv("VIGNEMALE_SQLDB", raising=False)
    with pytest.raises(SQLError, match="VIGNEMALE_SQLDB_SANSDSN"):
        SQLDatabase("sansdsn").dsn


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST", PG or "")
    d = SQLDatabase("pytest")
    d.execute("DROP TABLE IF EXISTS vignemale_pytest")
    d.execute(
        """
        CREATE TABLE vignemale_pytest (
            id    BIGSERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            done  BOOLEAN NOT NULL DEFAULT FALSE,
            score DOUBLE PRECISION,
            meta  JSONB
        )
        """
    )
    yield d
    d.execute("DROP TABLE IF EXISTS vignemale_pytest")


@needs_pg
def test_execute_returns_rowcount(db):
    n = db.execute(
        "INSERT INTO vignemale_pytest (title) VALUES ($1), ($2)", "a", "b"
    )
    assert n == 2


@needs_pg
def test_query_types_roundtrip(db):
    db.execute(
        "INSERT INTO vignemale_pytest (title, done, score, meta) VALUES ($1, $2, $3, $4)",
        "typé",
        True,
        9.5,
        {"tags": ["a", "b"]},
    )
    row = db.query_row("SELECT * FROM vignemale_pytest")
    assert row["title"] == "typé"
    assert row["done"] is True
    assert row["score"] == 9.5
    assert row["meta"] == {"tags": ["a", "b"]}
    assert isinstance(row["id"], int)


@needs_pg
def test_null_roundtrip(db):
    db.execute("INSERT INTO vignemale_pytest (title, score) VALUES ($1, $2)", "x", None)
    assert db.query_row("SELECT score FROM vignemale_pytest")["score"] is None


@needs_pg
def test_query_row_none_when_empty(db):
    assert db.query_row("SELECT * FROM vignemale_pytest WHERE id = $1", 424242) is None


@needs_pg
def test_sql_error_is_surfaced(db):
    with pytest.raises(SQLError, match="(?i)table_inexistante"):
        db.query("SELECT * FROM table_inexistante")


@needs_pg
def test_bad_dsn_is_sql_error(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_BROKEN", "postgres://nobody@127.0.0.1:1/nope")
    with pytest.raises(SQLError):
        SQLDatabase("broken").query("SELECT 1")
