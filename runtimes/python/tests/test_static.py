"""static_files: front served by the Rust core (SPA fallback) + API alongside."""

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


def test_index_served_at_root(app):
    status, ctype, body = get(app, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert "Rust core" in body


def test_assets_with_correct_content_type(app):
    status, ctype, body = get(app, "/app.js")
    assert status == 200 and "javascript" in ctype
    assert "fetch(" in body
    status, ctype, _ = get(app, "/style.css")
    assert status == 200 and "css" in ctype


def test_fallback_spa(app):
    """Unknown route → index.html (client-side routing, Next.js-export style)."""
    status, ctype, body = get(app, "/some/spa/route")
    assert status == 200 and ctype.startswith("text/html")
    assert "Rust core" in body


def test_api_coexists_with_the_front(app):
    status, ctype, body = get(app, "/api/hello?name=Jacques")
    assert status == 200 and "json" in ctype
    assert json.loads(body) == {"message": "Hello Jacques!"}


def test_healthz_takes_priority_over_the_fallback(app):
    status, _, body = get(app, "/__vignemale/healthz")
    assert status == 200 and json.loads(body)["code"] == "ok"
