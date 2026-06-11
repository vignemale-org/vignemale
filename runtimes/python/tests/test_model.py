"""vignemale.model : tables Pydantic, CRUD typé, migration additive."""

import os
from typing import Optional

import pytest

from vignemale import SQLDatabase
from vignemale.datamodel import Table, _tables, sql

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="pose VIGNEMALE_TEST_PG (DSN Postgres) pour l'activer"
)


class Gadget(Table):
    __database__ = "pytest_model"
    __tablename__ = "vgm_gadgets"

    id: Optional[int] = None
    name: str
    tags: dict = {}
    active: bool = True
    score: Optional[float] = None


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST_MODEL", PG or "")
    if PG:
        SQLDatabase("pytest_model").execute('DROP TABLE IF EXISTS "vgm_gadgets"')
        Gadget._Table__ensured = False
    yield


def test_table_registered():
    assert Gadget in _tables
    assert Gadget.__tablename__ == "vgm_gadgets"


@needs_pg
def test_crud_typed_roundtrip():
    g = Gadget.create(name="boussole", tags={"alt": 3298}, score=9.5)
    assert isinstance(g, Gadget) and isinstance(g.id, int)

    back = Gadget.get(g.id)
    assert back.name == "boussole"
    assert back.tags == {"alt": 3298}
    assert back.active is True
    assert back.score == 9.5

    back.name = "piolet"
    back.score = None
    back.save()
    assert Gadget.get(g.id).name == "piolet"
    assert Gadget.get(g.id).score is None

    assert Gadget.count() == 1
    assert Gadget.find_one(name="piolet").id == g.id
    assert Gadget.find_one(name="inexistant") is None

    back.delete()
    assert Gadget.get(g.id) is None


@needs_pg
def test_validation_pydantic_au_create():
    with pytest.raises(Exception):  # name requis
        Gadget.create(tags={})


@needs_pg
def test_requete_sql_custom_attachee_a_la_table():
    """`sql(...)` attache une requête custom à la table — typée au retour,
    ou brute (raw=True) pour les agrégats. Le CRUD standard reste au core."""

    class Outil(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None

        meilleurs = sql(
            "SELECT * FROM vgm_gadgets WHERE score >= $1 ORDER BY score DESC"
        )
        stats = sql(
            "SELECT count(*) AS n, max(score) AS top FROM vgm_gadgets", raw=True
        )

    Outil.create(name="piolet", score=5.0)
    Outil.create(name="corde", score=9.0)
    Outil.create(name="gourde", score=1.0)

    tops = Outil.meilleurs(4.0)
    assert [t.name for t in tops] == ["corde", "piolet"]
    assert isinstance(tops[0], Outil)  # lignes re-typées dans le modèle

    (s,) = Outil.stats()
    assert s["n"] == 3 and s["top"] == 9.0


@needs_pg
def test_injection_impossible_par_nom_de_colonne():
    """La whitelist des colonnes vit dans le CORE : un nom inconnu est rejeté."""
    from vignemale import SQLError

    with pytest.raises(SQLError, match="colonne inconnue"):
        Gadget.find(**{"nexiste_pas": 1})


@needs_pg
def test_migration_additive():
    Gadget.create(name="v1")  # crée la table version 1

    class GadgetV2(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"  # même table, colonne en plus

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None
        color: Optional[str] = None  # ← nouvelle colonne

    g = GadgetV2.create(name="v2", color="rouge")
    assert GadgetV2.get(g.id).color == "rouge"
    # les anciennes lignes ont color NULL
    assert GadgetV2.find_one(name="v1").color is None
