"""`vignemale check --sql`: validation of sql() queries via PREPARE —
the sqlx::query! mechanism, moved to check time (so in CI)."""

import os
import subprocess
import sys
from typing import Optional

import pytest

from conftest import EXAMPLES, HERE

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres DSN) to enable"
)


@needs_pg
def test_prepare_validates_types_and_detects_errors(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST_MODEL", PG)
    monkeypatch.setenv("VIGNEMALE_SQLDB", PG)  # for the tables of the other tests
    from vignemale.datamodel import Table, check_sql_queries, sql

    class Inspect(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None

        good = sql(
            "SELECT * FROM vgm_gadgets WHERE name = $n AND score >= $s",
            n=str,
            s=float,
        )
        broken = sql("SELECT nonexistent_column FROM vgm_gadgets")

    report = {
        r["query"]: r for r in check_sql_queries() if r["query"].startswith("Inspect.")
    }
    good = report["Inspect.good"]
    assert good["ok"] is True
    assert good["params"] == ["text", "float8"]  # types inferred by Postgres
    assert {c["name"] for c in good["columns"]} >= {"id", "name", "score"}

    broken = report["Inspect.broken"]
    assert broken["ok"] is False
    assert "nonexistent_column" in broken["error"]


@needs_pg
def test_cli_check_sql_ok_on_boutique():
    env = dict(os.environ, VIGNEMALE_SQLDB=PG)
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "check", "--sql",
         os.path.join(EXAMPLES, "boutique.py")],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "✓ Product.affordable" in r.stdout
    assert "float8" in r.stdout  # the inferred type of $max


@needs_pg
def test_cli_check_sql_fails_on_broken_query():
    env = dict(os.environ, VIGNEMALE_SQLDB=PG)
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "check", "--sql",
         os.path.join(HERE, "app_sql_broken.py")],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode != 0
    assert "✗ Broken.boom" in r.stdout
    assert "invalid" in r.stderr
