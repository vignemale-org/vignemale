"""GDPR: data map, export of a person, right to be forgotten."""

import json
import os
import subprocess
import sys
from typing import Optional

import pytest

from conftest import EXAMPLES
from vignemale import SQLDatabase, gdpr
from vignemale.datamodel import PII, Table

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres DSN) to enable"
)


class Person(Table):
    __database__ = "pytest_model"
    __tablename__ = "vgm_persons"
    __subject__ = "id"

    id: Optional[int] = None
    email: str = PII(purpose="contact")
    note: str = "nothing to report"


class Purchase(Table):
    __database__ = "pytest_model"
    __tablename__ = "vgm_purchases"
    __subject__ = "person_id"
    __on_forget__ = "anonymize"  # the row stays (stats), the PII is redacted

    id: Optional[int] = None
    person_id: int
    product: str = PII(purpose="purchase history")
    amount: float = 0.0


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST_MODEL", PG or "")
    if PG:
        db = SQLDatabase("pytest_model")
        db.execute('DROP TABLE IF EXISTS "vgm_persons"')
        db.execute('DROP TABLE IF EXISTS "vgm_purchases"')
        Person._Table__ensured = False
        Purchase._Table__ensured = False
    yield


def test_data_map_inventories_the_pii():
    dmap = {t["table"]: t for t in gdpr.data_map()}
    p = dmap["vgm_persons"]
    assert p["subject"] == "id"
    fields = {f["name"]: f for f in p["fields"]}
    assert fields["email"]["pii"] is True
    assert fields["email"]["purpose"] == "contact"
    assert fields["note"]["pii"] is False
    assert dmap["vgm_purchases"]["on_forget"] == "anonymize"


@needs_pg
def test_export_and_forget():
    ada = Person.create(email="ada@example.com")
    Person.create(email="bob@example.com")  # another subject, untouched
    Purchase.create(person_id=ada.id, product="ice-axe", amount=89.9)
    Purchase.create(person_id=ada.id, product="60m rope", amount=149.0)

    # right of access: all of Ada's data, table by table
    export = gdpr.export_subject(ada.id)
    assert [r["email"] for r in export["vgm_persons"]] == ["ada@example.com"]
    assert {r["product"] for r in export["vgm_purchases"]} == {"ice-axe", "60m rope"}

    # dry-run: summary without touching anything
    summary = gdpr.forget_subject(ada.id, dry_run=True)
    assert summary["vgm_persons"]["action"] == "delete"
    assert summary["vgm_purchases"] == {"rows": 2, "action": "anonymize", "dry_run": True}
    assert Person.get(ada.id) is not None

    # right to be forgotten: delete for the person, anonymize for the purchases
    gdpr.forget_subject(ada.id)
    assert Person.get(ada.id) is None
    remaining = Purchase.find(person_id=ada.id)
    assert len(remaining) == 2  # the rows stay…
    assert {a.product for a in remaining} == {"[redacted]"}  # …redacted
    assert {a.amount for a in remaining} == {89.9, 149.0}  # the non-PII stays
    # and Bob hasn't moved
    assert Person.find_one(email="bob@example.com") is not None


def test_cli_gdpr_map_on_copilot():
    """`vignemale gdpr map`: the app's data map, without touching the DB."""
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "gdpr", "map",
         os.path.join(EXAMPLES, "copilot")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    dmap = {t["table"]: t for t in json.loads(r.stdout)}
    users = dmap["users"]
    assert users["database"] == "users" and users["subject"] == "id"
    pii = {f["name"] for f in users["fields"] if f["pii"]}
    assert {"email", "name", "token"} <= pii
    assert dmap["messages"]["on_forget"] == "anonymize"
