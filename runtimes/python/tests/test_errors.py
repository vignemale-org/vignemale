"""Validation Pydantic (422), HTTPError (404) et streaming — via `vignemale run`
sur examples/assistant.py (couvre aussi le chemin CLI)."""

import os
import sys

import pytest

from conftest import EXAMPLES, Server, free_port, request, sse


@pytest.fixture(scope="module")
def assistant():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [
            sys.executable,
            "-m",
            "vignemale.cli",
            "run",
            os.path.join(EXAMPLES, "assistant.py"),
            "--addr",
            addr,
        ],
        addr,
    )
    yield addr
    srv.stop()


def test_health(assistant):
    assert request(assistant, "/health") == (200, {"ok": True})


def test_typed_body_validated(assistant):
    status, reply = request(assistant, "/ask", {"question": "ça marche ?"})
    assert status == 200
    assert "ça marche ?" in reply["answer"]
    assert reply["lang"] == "fr"  # défaut Pydantic appliqué


def test_missing_field_is_invalid_argument(assistant):
    status, body = request(assistant, "/ask", {"lang": "fr"})
    assert status == 400
    assert body["code"] == "invalid_argument"
    assert body["details"][0]["type"] == "missing"
    assert body["details"][0]["loc"] == ["question"]


def test_missing_body_is_invalid_argument(assistant):
    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"http://{assistant}/ask", method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "un body requis manquant doit être rejeté"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        assert _json.loads(e.read())["code"] == "invalid_argument"


def test_http_error_is_404(assistant):
    status, body = request(assistant, "/notes/999")
    assert status == 404
    assert body == {"code": "not_found", "message": "note 999 introuvable", "details": None}


def test_streaming(assistant):
    chunks = sse(assistant, "/chat", {"prompt": "bonjour"})
    assert len(chunks) > 1  # plusieurs fragments, pas une réponse d'un bloc
    assert "bonjour" in " ".join(chunks)
