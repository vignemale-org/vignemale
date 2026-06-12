"""RGPD : carte des données, export d'une personne, droit à l'oubli."""

import json
import os
import subprocess
import sys
from typing import Optional

import pytest

from conftest import EXAMPLES
from vignemale import SQLDatabase, rgpd
from vignemale.datamodel import PII, Table

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="pose VIGNEMALE_TEST_PG (DSN Postgres) pour l'activer"
)


class Personne(Table):
    __database__ = "pytest_model"
    __tablename__ = "vgm_personnes"
    __subject__ = "id"

    id: Optional[int] = None
    email: str = PII(purpose="contact")
    note: str = "rien à signaler"


class Achat(Table):
    __database__ = "pytest_model"
    __tablename__ = "vgm_achats"
    __subject__ = "personne_id"
    __on_forget__ = "anonymize"  # la ligne reste (stats), le PII est caviardé

    id: Optional[int] = None
    personne_id: int
    produit: str = PII(purpose="historique d'achat")
    montant: float = 0.0


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_PYTEST_MODEL", PG or "")
    if PG:
        db = SQLDatabase("pytest_model")
        db.execute('DROP TABLE IF EXISTS "vgm_personnes"')
        db.execute('DROP TABLE IF EXISTS "vgm_achats"')
        Personne._Table__ensured = False
        Achat._Table__ensured = False
    yield


def test_data_map_inventorie_les_pii():
    carte = {t["table"]: t for t in rgpd.data_map()}
    p = carte["vgm_personnes"]
    assert p["subject"] == "id"
    fields = {f["name"]: f for f in p["fields"]}
    assert fields["email"]["pii"] is True
    assert fields["email"]["purpose"] == "contact"
    assert fields["note"]["pii"] is False
    assert carte["vgm_achats"]["on_forget"] == "anonymize"


@needs_pg
def test_export_et_oubli():
    ada = Personne.create(email="ada@example.com")
    Personne.create(email="bob@example.com")  # un autre sujet, intouché
    Achat.create(personne_id=ada.id, produit="piolet", montant=89.9)
    Achat.create(personne_id=ada.id, produit="corde 60m", montant=149.0)

    # droit d'accès : toutes les données d'Ada, table par table
    export = rgpd.export_subject(ada.id)
    assert [r["email"] for r in export["vgm_personnes"]] == ["ada@example.com"]
    assert {r["produit"] for r in export["vgm_achats"]} == {"piolet", "corde 60m"}

    # dry-run : bilan sans rien toucher
    bilan = rgpd.forget_subject(ada.id, dry_run=True)
    assert bilan["vgm_personnes"]["action"] == "delete"
    assert bilan["vgm_achats"] == {"rows": 2, "action": "anonymize", "dry_run": True}
    assert Personne.get(ada.id) is not None

    # droit à l'oubli : delete pour la personne, anonymize pour les achats
    rgpd.forget_subject(ada.id)
    assert Personne.get(ada.id) is None
    restants = Achat.find(personne_id=ada.id)
    assert len(restants) == 2  # les lignes restent…
    assert {a.produit for a in restants} == {"[effacé]"}  # …caviardées
    assert {a.montant for a in restants} == {89.9, 149.0}  # le non-PII reste
    # et Bob n'a pas bougé
    assert Personne.find_one(email="bob@example.com") is not None


def test_cli_rgpd_map_sur_copilote():
    """`vignemale rgpd map` : la carte des données de l'app, sans toucher la DB."""
    r = subprocess.run(
        [sys.executable, "-m", "vignemale_cli", "rgpd", "map",
         os.path.join(EXAMPLES, "copilote")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    carte = {t["table"]: t for t in json.loads(r.stdout)}
    users = carte["users"]
    assert users["database"] == "users" and users["subject"] == "id"
    pii = {f["name"] for f in users["fields"] if f["pii"]}
    assert {"email", "name", "token"} <= pii
    assert carte["messages"]["on_forget"] == "anonymize"