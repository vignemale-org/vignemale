"""`vignemale check --sql` : validation des requêtes sql() par PREPARE —
le mécanisme de sqlx::query!, déplacé au moment check (donc en CI)."""

import os
import subprocess
import sys
from typing import Optional

import pytest

from conftest import EXAMPLES, HERE

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="pose VIGNEMALE_TEST_PG (DSN Postgres) pour l'activer"
)


@needs_pg
def test_prepare_valide_types_et_detecte_les_erreurs(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST_MODEL", PG)
    monkeypatch.setenv("VIGNEMALE_SQLDB", PG)  # pour les tables des autres tests
    from vignemale.datamodel import Table, check_sql_queries, sql

    class Inspecte(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None

        bonne = sql(
            "SELECT * FROM vgm_gadgets WHERE name = $n AND score >= $s",
            n=str,
            s=float,
        )
        cassee = sql("SELECT colonne_inexistante FROM vgm_gadgets")

    report = {
        r["query"]: r for r in check_sql_queries() if r["query"].startswith("Inspecte.")
    }
    bonne = report["Inspecte.bonne"]
    assert bonne["ok"] is True
    assert bonne["params"] == ["text", "float8"]  # types inférés par Postgres
    assert {c["name"] for c in bonne["columns"]} >= {"id", "name", "score"}

    cassee = report["Inspecte.cassee"]
    assert cassee["ok"] is False
    assert "colonne_inexistante" in cassee["error"]


@needs_pg
def test_cli_check_sql_ok_sur_boutique():
    env = dict(os.environ, VIGNEMALE_SQLDB=PG)
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "check", "--sql",
         os.path.join(EXAMPLES, "boutique.py")],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "✓ Produit.abordables" in r.stdout
    assert "float8" in r.stdout  # le type inféré de $max


@needs_pg
def test_cli_check_sql_echoue_sur_requete_cassee():
    env = dict(os.environ, VIGNEMALE_SQLDB=PG)
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "check", "--sql",
         os.path.join(HERE, "app_sql_casse.py")],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert r.returncode != 0
    assert "✗ Casse.boom" in r.stdout
    assert "invalide" in r.stderr
