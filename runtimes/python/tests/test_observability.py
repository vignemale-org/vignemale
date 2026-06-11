"""Observabilité : logs JSON par requête, request-id, erreurs avec traceback."""

import json
import os
import sys
import urllib.request

import pytest

from conftest import HERE, Server, free_port, request


@pytest.fixture()
def hello():
    """Serveur app_hello avec stderr capturé (les logs y vont)."""
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server(
        [sys.executable, os.path.join(HERE, "app_hello.py")], addr, env=env, capture=True
    )
    yield srv
    srv.stop()


def logs_of(srv) -> list:
    srv.stop()
    lines = srv.proc.stderr.read().decode().splitlines()
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # lignes non-JSON éventuelles (warnings python, etc.)
    return out


def test_request_id_header_and_log_line(hello):
    with urllib.request.urlopen(f"http://{hello.addr}/hello", timeout=5) as r:
        rid = r.headers["x-vignemale-request-id"]
    assert rid, "chaque réponse doit porter x-vignemale-request-id"

    logs = logs_of(hello)
    (line,) = [l for l in logs if l.get("request_id") == rid]
    assert line["level"] == "INFO"
    assert line["endpoint"] == "hello"
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert "duration_ms" in line


def test_startup_log(hello):
    logs = logs_of(hello)
    (line,) = [l for l in logs if l.get("message") == "serveur démarré"]
    assert line["endpoints"] == 5


def test_unhandled_exception_500_with_request_id(hello):
    status, body = request(hello.addr, "/boom")
    assert status == 500
    assert body["error"] == "internal error"
    rid = body["request_id"]

    logs = logs_of(hello)
    # deux lignes ERROR corrélées par le request_id : le traceback (app)…
    (err,) = [
        l
        for l in logs
        if l.get("level") == "ERROR"
        and l.get("request_id") == rid
        and l.get("target") == "vignemale::app"
    ]
    assert "ValueError: explosion contrôlée" in err["traceback"]
    # …et la ligne de requête (api) en statut 500
    (req_line,) = [
        l for l in logs if l.get("endpoint") == "boom" and l.get("request_id") == rid
    ]
    assert req_line["level"] == "ERROR"
    assert req_line["status"] == 500


def test_http_error_is_not_a_500_log(hello):
    # un HTTPError volontaire (4xx) ne doit pas générer de log ERROR
    status, _ = request(hello.addr, "/greet/Jacques")
    assert status == 200
    logs = logs_of(hello)
    assert not [l for l in logs if l.get("level") == "ERROR"]


def test_python_log_api(capfd):
    from vignemale import log

    log.info("commande créée", order_id=42)
    log.debug("invisible au niveau info")
    out = capfd.readouterr().err.strip().splitlines()
    (line,) = [json.loads(l) for l in out]
    assert line["level"] == "INFO"
    assert line["message"] == "commande créée"
    assert line["order_id"] == 42
    assert line["target"] == "vignemale::app"
    assert "timestamp" in line
