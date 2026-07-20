"""SQLDatabase (Postgres via the Rust core).

DSN resolution can be tested without a database; the rest needs a Postgres:

    docker run -d --name vignemale-pg -p 5433:5432 -e POSTGRES_PASSWORD=vignemale postgres:16
    VIGNEMALE_TEST_PG=postgres://postgres:vignemale@127.0.0.1:5433/postgres python -m pytest tests/ -k sqldb
"""

import os

import pytest

from vignemale import SQLDatabase, SQLError

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres DSN) to enable"
)


def test_dsn_resolution_specific_wins(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_MYDB", "postgres://specific")
    monkeypatch.setenv("VIGNEMALE_SQLDB", "postgres://common")
    assert SQLDatabase("mydb").dsn == "postgres://specific"
    assert SQLDatabase("other").dsn == "postgres://common"


def test_dsn_missing_is_clear_error(monkeypatch):
    monkeypatch.delenv("VIGNEMALE_SQLDB_NODSN", raising=False)
    monkeypatch.delenv("VIGNEMALE_SQLDB", raising=False)
    with pytest.raises(SQLError, match="VIGNEMALE_SQLDB_NODSN"):
        SQLDatabase("nodsn").dsn


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
        "typed",
        True,
        9.5,
        {"tags": ["a", "b"]},
    )
    row = db.query_row("SELECT * FROM vignemale_pytest")
    assert row["title"] == "typed"
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
    with pytest.raises(SQLError, match="(?i)nonexistent_table"):
        db.query("SELECT * FROM nonexistent_table")


@needs_pg
def test_bad_dsn_is_sql_error(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_BROKEN", "postgres://nobody@127.0.0.1:1/nope")
    with pytest.raises(SQLError):
        SQLDatabase("broken").query("SELECT 1")


# --- transactions (Encore-ported: COMMIT/ROLLBACK, atomicity) ---


@needs_pg
def test_transaction_commit(db):
    with db.transaction() as tx:
        tx.execute("INSERT INTO vignemale_pytest (title) VALUES ($1)", "in the tx")
        # visible INSIDE the transaction…
        assert tx.query_row("SELECT count(*) AS n FROM vignemale_pytest")["n"] == 1
    # …and after the COMMIT
    assert db.query_row("SELECT title FROM vignemale_pytest")["title"] == "in the tx"


@needs_pg
def test_transaction_rollback_on_exception(db):
    with pytest.raises(ValueError):
        with db.transaction() as tx:
            tx.execute("INSERT INTO vignemale_pytest (title) VALUES ($1)", "ghost")
            raise ValueError("business boom")
    # the exception rolled everything back
    assert db.query_row("SELECT count(*) AS n FROM vignemale_pytest")["n"] == 0


@needs_pg
def test_transaction_isolation(db):
    with db.transaction() as tx:
        tx.execute("INSERT INTO vignemale_pytest (title) VALUES ($1)", "invisible")
        # not yet visible OUTSIDE the transaction
        assert db.query_row("SELECT count(*) AS n FROM vignemale_pytest")["n"] == 0
    assert db.query_row("SELECT count(*) AS n FROM vignemale_pytest")["n"] == 1


# --- rich types (ported from Encore's val.rs) ---


@needs_pg
def test_numeric_precision_preserved(db):
    db.execute("ALTER TABLE vignemale_pytest ADD COLUMN price NUMERIC(12,2)")
    db.execute(
        "INSERT INTO vignemale_pytest (title, price) VALUES ($1, $2)", "n", "12345678.91"
    )
    # NUMERIC travels as a string: no float loss
    assert db.query_row("SELECT price FROM vignemale_pytest")["price"] == "12345678.91"


@needs_pg
def test_bytea_base64(db):
    db.execute("ALTER TABLE vignemale_pytest ADD COLUMN blob BYTEA")
    db.execute("INSERT INTO vignemale_pytest (title, blob) VALUES ($1, $2)", "b", "hello")
    import base64

    raw = db.query_row("SELECT blob FROM vignemale_pytest")["blob"]
    assert base64.b64decode(raw) == b"hello"


@needs_pg
def test_arrays(db):
    db.execute("ALTER TABLE vignemale_pytest ADD COLUMN tags TEXT[]")
    db.execute("ALTER TABLE vignemale_pytest ADD COLUMN scores BIGINT[]")
    db.execute(
        "INSERT INTO vignemale_pytest (title, tags, scores) "
        "VALUES ($1, ARRAY['a','b'], ARRAY[1,2,3])",
        "arr",
    )
    row = db.query_row("SELECT tags, scores FROM vignemale_pytest")
    assert row["tags"] == ["a", "b"]
    assert row["scores"] == [1, 2, 3]


@needs_pg
def test_time_and_date(db):
    row = db.query_row("SELECT TIME '14:30:00' AS t, DATE '2026-06-12' AS d")
    assert row["t"].startswith("14:30:00")
    assert row["d"] == "2026-06-12"
