"""API server (app_hello.py): unary, path param, JSON body, SSE streaming."""

import os
import sys

import pytest

from conftest import HERE, Server, free_port, request, sse


@pytest.fixture(scope="module")
def hello():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server([sys.executable, os.path.join(HERE, "app_hello.py")], addr, env=env)
    yield addr
    srv.stop()


def test_unary(hello):
    assert request(hello, "/hello") == (200, {"msg": "hello from vignemale"})


def test_path_param(hello):
    assert request(hello, "/greet/Jacques") == (200, {"hello": "Jacques"})


def test_body_json(hello):
    body = {"x": 1, "k": "v"}
    assert request(hello, "/echo", body) == (200, {"you_sent": body})


def test_streaming_sse(hello):
    assert sse(hello, "/stream") == "this is a vignemale stream token by token".split(" ")


def test_query_params(hello):
    assert request(hello, "/search?q=midi&limit=3") == (200, {"q": "midi", "limit": "3"})
    # missing param → default on the handler side
    assert request(hello, "/search?q=x") == (200, {"q": "x", "limit": "10"})


def test_headers(hello):
    import json as _json
    import urllib.request

    req = urllib.request.Request(
        f"http://{hello}/whoami", headers={"X-Client": "pytest"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert _json.loads(r.read()) == {"client": "pytest"}


def test_healthz(hello):
    status, body = request(hello, "/__vignemale/healthz")
    assert status == 200
    assert body["code"] == "ok"


def test_unknown_route_is_structured_404(hello):
    status, body = request(hello, "/does/not/exist")
    assert status == 404
    assert body["code"] == "not_found"


def test_malformed_json_body_is_400(hello):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"http://{hello}/echo", data=b"{not json")
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "an invalid JSON body must be rejected"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        import json as _json

        assert _json.loads(e.read())["code"] == "invalid_argument"


def test_cors_preflight(hello):
    import urllib.request

    req = urllib.request.Request(
        f"http://{hello}/hello",
        method="OPTIONS",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers["access-control-allow-origin"] == "*"
