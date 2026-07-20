"""Observability: JSON logs per request, request-id, errors with traceback."""

import json
import os
import sys
import urllib.request

import pytest

from conftest import HERE, Server, free_port, request


@pytest.fixture()
def hello():
    """app_hello server with stderr captured (that's where the logs go)."""
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
            pass  # possible non-JSON lines (python warnings, etc.)
    return out


def test_request_id_header_and_log_line(hello):
    with urllib.request.urlopen(f"http://{hello.addr}/hello", timeout=5) as r:
        rid = r.headers["x-vignemale-request-id"]
    assert rid, "every response must carry x-vignemale-request-id"

    logs = logs_of(hello)
    (line,) = [l for l in logs if l.get("request_id") == rid]
    assert line["level"] == "INFO"
    assert line["endpoint"] == "hello"
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert "duration_ms" in line


def test_startup_log(hello):
    logs = logs_of(hello)
    (line,) = [l for l in logs if l.get("message") == "server started"]
    assert line["endpoints"] == 10


def test_unhandled_exception_500_with_request_id(hello):
    status, body = request(hello.addr, "/boom")
    assert status == 500
    assert body["code"] == "internal"
    rid = body["details"]["request_id"]

    logs = logs_of(hello)
    # two ERROR lines correlated by request_id: the traceback (app)…
    (err,) = [
        l
        for l in logs
        if l.get("level") == "ERROR"
        and l.get("request_id") == rid
        and l.get("target") == "vignemale::app"
    ]
    assert "ValueError: controlled explosion" in err["traceback"]
    # …and the request line (api) with status 500
    (req_line,) = [
        l for l in logs if l.get("endpoint") == "boom" and l.get("request_id") == rid
    ]
    assert req_line["level"] == "ERROR"
    assert req_line["status"] == 500


def test_http_error_is_not_a_500_log(hello):
    # a deliberate HTTPError (4xx) must not generate an ERROR log
    status, _ = request(hello.addr, "/greet/Jacques")
    assert status == 200
    logs = logs_of(hello)
    assert not [l for l in logs if l.get("level") == "ERROR"]


def test_python_log_api(capfd):
    from vignemale import log

    log.info("order created", order_id=42)
    log.debug("invisible at info level")
    out = capfd.readouterr().err.strip().splitlines()
    (line,) = [json.loads(l) for l in out]
    assert line["level"] == "INFO"
    assert line["message"] == "order created"
    assert line["order_id"] == 42
    assert line["target"] == "vignemale::app"
    assert "timestamp" in line
