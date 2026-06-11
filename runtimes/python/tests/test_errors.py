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


def test_missing_field_is_422(assistant):
    status, body = request(assistant, "/ask", {"lang": "fr"})
    assert status == 422
    assert body["detail"][0]["type"] == "missing"
    assert body["detail"][0]["loc"] == ["question"]


def test_http_error_is_404(assistant):
    status, body = request(assistant, "/notes/999")
    assert status == 404
    assert body == {"detail": "note 999 introuvable"}


def test_streaming(assistant):
    chunks = sse(assistant, "/chat", {"prompt": "bonjour"})
    assert len(chunks) > 1  # plusieurs fragments, pas une réponse d'un bloc
    assert "bonjour" in " ".join(chunks)
