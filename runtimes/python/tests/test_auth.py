"""Authentification : @auth_handler + @api(auth=True), façon Encore."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from conftest import HERE, Server, free_port, request, sse


@pytest.fixture(scope="module")
def app():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server([sys.executable, os.path.join(HERE, "app_auth.py")], addr, env=env)
    yield addr
    srv.stop()


def get(addr, path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://{addr}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_public_sans_token(app):
    assert get(app, "/public") == (200, {"open": True})


def test_private_sans_token_401(app):
    status, body = get(app, "/private")
    assert status == 401
    assert body["code"] == "unauthenticated"


def test_private_mauvais_token_401(app):
    status, body = get(app, "/private", token="abracadabra")
    assert status == 401
    assert body["code"] == "unauthenticated"
    assert body["message"] == "token invalide"


def test_private_bon_token(app):
    assert get(app, "/private", token="sesame") == (
        200,
        {"user": "u-42", "role": "admin"},
    )


def test_token_via_query(app):
    # pour les clients sans en-têtes (EventSource…)
    assert get(app, "/private?token=sesame")[0] == 200


def test_protege_sans_declarer_auth(app):
    # le handler ne déclare pas `auth` : la protection s'applique quand même
    assert get(app, "/private-opaque")[0] == 401
    assert get(app, "/private-opaque", token="sesame") == (200, {"ok": True})


def test_stream_protege(app):
    chunks = sse(app, "/private-stream?token=sesame")
    assert chunks == ["bienvenue u-42"]
    # sans token : VRAI 401, avant même d'ouvrir le flux (l'auth est jouée
    # par le core, pas par le handler)
    try:
        sse(app, "/private-stream")
        assert False, "un stream protégé sans token doit renvoyer 401"
    except urllib.error.HTTPError as e:
        assert e.code == 401
        assert json.loads(e.read())["code"] == "unauthenticated"


def test_app_protegee_sans_auth_handler_refuse_de_demarrer():
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "app_auth_broken.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode != 0
    assert "@auth_handler" in r.stderr
