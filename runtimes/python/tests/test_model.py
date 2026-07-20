"""vignemale.model: Pydantic tables, typed CRUD, additive migration."""

import os
from typing import Optional

import pytest

from vignemale import SQLDatabase
from vignemale.datamodel import Table, _tables, sql

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres DSN) to enable"
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
    g = Gadget.create(name="compass", tags={"alt": 3298}, score=9.5)
    assert isinstance(g, Gadget) and isinstance(g.id, int)

    back = Gadget.get(g.id)
    assert back.name == "compass"
    assert back.tags == {"alt": 3298}
    assert back.active is True
    assert back.score == 9.5

    back.name = "ice-axe"
    back.score = None
    back.save()
    assert Gadget.get(g.id).name == "ice-axe"
    assert Gadget.get(g.id).score is None

    assert Gadget.count() == 1
    assert Gadget.find_one(name="ice-axe").id == g.id
    assert Gadget.find_one(name="nonexistent") is None

    back.delete()
    assert Gadget.get(g.id) is None


@needs_pg
def test_validation_pydantic_on_create():
    with pytest.raises(Exception):  # name required
        Gadget.create(tags={})


@needs_pg
def test_custom_sql_query_attached_to_the_table():
    """`sql(...)` attaches a custom query to the table — typed on return,
    or raw (raw=True) for aggregates. Standard CRUD stays in the core."""

    class Tool(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None

        best = sql(
            "SELECT * FROM vgm_gadgets WHERE score >= $1 ORDER BY score DESC"
        )
        stats = sql(
            "SELECT count(*) AS n, max(score) AS top FROM vgm_gadgets", raw=True
        )

    Tool.create(name="ice-axe", score=5.0)
    Tool.create(name="rope", score=9.0)
    Tool.create(name="flask", score=1.0)

    tops = Tool.best(4.0)
    assert [t.name for t in tops] == ["rope", "ice-axe"]
    assert isinstance(tops[0], Tool)  # rows re-typed into the model

    (s,) = Tool.stats()
    assert s["n"] == 3 and s["top"] == 9.0


@needs_pg
def test_sql_named_typed_parameters():
    """`sql(..., name=type)`: $name placeholders, Pydantic validation/coercion."""

    class Tool2(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None

        between = sql(
            "SELECT * FROM vgm_gadgets WHERE score >= $min AND score <= $max "
            "ORDER BY score",
            min=float,
            max=float,
        )
        like = sql(  # repeated $name → same placeholder
            "SELECT * FROM vgm_gadgets WHERE name = $n OR name = upper($n)", n=str
        )

    Tool2.create(name="a", score=2.0)
    Tool2.create(name="b", score=5.0)
    Tool2.create(name="c", score=9.0)

    assert [o.name for o in Tool2.between(min=3, max=8)] == ["b"]  # int → float coerced
    assert [o.name for o in Tool2.between("1", "6")] == ["a", "b"]  # strings coerced
    assert [o.name for o in Tool2.like(n="a")] == ["a"]

    with pytest.raises(TypeError, match="missing"):
        Tool2.between(min=1)
    with pytest.raises(TypeError, match="unknown"):
        Tool2.between(min=1, max=2, oops=3)
    with pytest.raises(Exception):  # pydantic validation: not a float
        Tool2.between(min="not a number", max=2)


def test_sql_inconsistent_declaration_rejected():
    """Fail fast at declaration: parameter not declared or never used."""
    with pytest.raises(TypeError, match="not declared"):
        sql("SELECT * FROM x WHERE a = $a AND b = $b", a=str)  # $b missing
    with pytest.raises(TypeError, match="absent"):
        sql("SELECT * FROM x WHERE a = $a", a=str, b=int)  # b never used


@needs_pg
def test_injection_impossible_by_column_name():
    """The column whitelist lives in the CORE: an unknown name is rejected."""
    from vignemale import SQLError

    with pytest.raises(SQLError, match="unknown column"):
        Gadget.find(**{"does_not_exist": 1})


@needs_pg
def test_migration_additive():
    Gadget.create(name="v1")  # creates table version 1

    class GadgetV2(Table):
        __database__ = "pytest_model"
        __tablename__ = "vgm_gadgets"  # same table, one extra column

        id: Optional[int] = None
        name: str
        tags: dict = {}
        active: bool = True
        score: Optional[float] = None
        color: Optional[str] = None  # ← new column

    g = GadgetV2.create(name="v2", color="red")
    assert GadgetV2.get(g.id).color == "red"
    # old rows have color NULL
    assert GadgetV2.find_one(name="v1").color is None
