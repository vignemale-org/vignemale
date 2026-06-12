"""ORM tiers (façon Encore) : connection_string + migrations .sql appliquées
au run. Vérifié avec SQLAlchemy sur l'exemple blog."""

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

from conftest import EXAMPLES, Server, free_port

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(not PG, reason="pose VIGNEMALE_TEST_PG")
try:
    import sqlalchemy  # noqa: F401
    HAS_SA = True
except ImportError:
    HAS_SA = False
needs_sa = pytest.mark.skipif(not HAS_SA, reason="sqlalchemy non installé")


def test_connection_string_est_le_dsn(monkeypatch):
    monkeypatch.setenv("VIGNEMALE_SQLDB_X", "postgres://u:p@h:5432/d")
    from vignemale import SQLDatabase

    db = SQLDatabase("x")
    assert db.connection_string == "postgres://u:p@h:5432/d" == db.dsn


def req(addr, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(f"http://{addr}{path}", data=body)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@needs_pg
@needs_sa
def test_blog_migrations_et_orm_tiers():
    import uuid

    # base dédiée au test (les migrations créent la table une fois)
    dbname = f"blog_test_{uuid.uuid4().hex[:8]}"
    import psycopg  # noqa

    admin = PG.rsplit("/", 1)[0] + "/postgres"
    eng_dsn = admin.replace("postgres://", "postgresql+psycopg://", 1)
    import sqlalchemy

    with sqlalchemy.create_engine(eng_dsn, isolation_level="AUTOCOMMIT").connect() as cx:
        cx.execute(sqlalchemy.text(f'CREATE DATABASE "{dbname}"'))

    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ,
               VIGNEMALE_SQLDB_BLOG=f"{PG.rsplit('/', 1)[0]}/{dbname}")
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "blog", "app.py"), "--addr", addr],
        addr, env=env, capture=True,
    )
    try:
        # les 2 migrations ont créé posts + colonne published
        s, created = req(addr, "/posts", {"title": "Hello", "body": "b"})
        assert s == 200 and isinstance(created["id"], int)
        s, listing = req(addr, "/posts")
        assert s == 200
        assert listing["posts"][0]["title"] == "Hello"
        assert listing["posts"][0]["published"] is False  # migration 002
    finally:
        srv.stop()
