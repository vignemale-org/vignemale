"""static_files : front servi par le core Rust (SPA fallback) + API à côté."""

import json
import os
import sys
import urllib.request

import pytest

from conftest import EXAMPLES, Server, free_port


@pytest.fixture(scope="module")
def app():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "fullstack", "app.py"), "--addr", addr],
        addr,
    )
    yield addr
    srv.stop()


def get(addr, path):
    with urllib.request.urlopen(f"http://{addr}{path}", timeout=5) as r:
        return r.status, r.headers.get("content-type", ""), r.read().decode()


def test_index_servi_a_la_racine(app):
    status, ctype, body = get(app, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert "core Rust" in body


def test_assets_avec_bon_content_type(app):
    status, ctype, body = get(app, "/app.js")
    assert status == 200 and "javascript" in ctype
    assert "fetch(" in body
    status, ctype, _ = get(app, "/style.css")
    assert status == 200 and "css" in ctype


def test_fallback_spa(app):
    """Route inconnue → index.html (routing côté client, façon Next.js export)."""
    status, ctype, body = get(app, "/une/route/de/spa")
    assert status == 200 and ctype.startswith("text/html")
    assert "core Rust" in body


def test_api_coexiste_avec_le_front(app):
    status, ctype, body = get(app, "/api/hello?name=Jacques")
    assert status == 200 and "json" in ctype
    assert json.loads(body) == {"message": "Bonjour Jacques !"}


def test_healthz_prioritaire_sur_le_fallback(app):
    status, _, body = get(app, "/__vignemale/healthz")
    assert status == 200 and json.loads(body)["code"] == "ok"
