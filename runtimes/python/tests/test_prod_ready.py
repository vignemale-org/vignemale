"""Prod-readiness: timeout, body limit, graceful shutdown with drain."""

import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import HERE, Server, free_port, request


@pytest.fixture(scope="module")
def hello():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server([sys.executable, os.path.join(HERE, "app_hello.py")], addr, env=env)
    yield addr
    srv.stop()


def test_timeout_per_endpoint(hello):
    """@api(timeout=0.5) on a 3 s handler → 504 deadline_exceeded."""
    t0 = time.time()
    status, body = request(hello, "/slow")
    assert status == 504
    assert body["code"] == "deadline_exceeded"
    assert time.time() - t0 < 2, "the response must be sent at the timeout, not at the end of the handler"


def test_body_limit_per_endpoint(hello):
    """@api(body_limit=1024) → a 4 KiB body is rejected with 413."""
    req = urllib.request.Request(f"http://{hello}/small", data=b"x" * 4096)
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "a too-large body must be rejected"
    except urllib.error.HTTPError as e:
        assert e.code == 413
        assert json.loads(e.read())["code"] == "resource_exhausted"
    # under the limit: OK
    req = urllib.request.Request(f"http://{hello}/small", data=b'{"a":1}')
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


@pytest.mark.skipif(sys.platform == "win32", reason="signaux POSIX")
def test_graceful_shutdown_drain():
    """SIGTERM during an in-flight request: it COMPLETES (200), then the
    process exits cleanly — no request cut off on redeploy."""
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr)
    srv = Server(
        [sys.executable, os.path.join(HERE, "app_hello.py")], addr, env=env, capture=True
    )
    result = {}

    def long_call():
        with urllib.request.urlopen(f"http://{addr}/work", timeout=10) as r:
            result["body"] = json.loads(r.read())

    t = threading.Thread(target=long_call)
    t.start()
    time.sleep(0.4)  # the request is in flight
    srv.proc.send_signal(signal.SIGTERM)
    t.join(timeout=10)

    assert result.get("body") == {"done": True}, "the in-flight request must complete"
    assert srv.proc.wait(timeout=10) == 0
    assert "stopped" in srv.proc.stdout.read().decode()

    # after shutdown: no more connections accepted
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://{addr}/hello", timeout=2)


@pytest.mark.skipif(sys.platform == "win32", reason="signaux POSIX")
def test_keep_accepting_load_balancer_window():
    """Encore-style shutdown sequence: on signal, healthz turns 503 but the
    server KEEPS accepting for VIGNEMALE_SHUTDOWN_KEEP_ACCEPTING — long enough
    for the load balancer to see the 503 and stop routing."""
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_ADDR=addr, VIGNEMALE_SHUTDOWN_KEEP_ACCEPTING="3")
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(HERE, "app_hello.py"), "--addr", addr],
        addr, env=env, capture=True,
    )

    def code(path):
        try:
            with urllib.request.urlopen(f"http://{addr}{path}", timeout=2) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    assert code("/__vignemale/healthz") == 200
    srv.proc.send_signal(signal.SIGINT)
    time.sleep(0.8)  # within the keep_accepting window (3 s)
    assert code("/__vignemale/healthz") == 503  # the LB sees the imminent shutdown
    assert code("/hello") == 200                 # but we still accept
    assert srv.proc.wait(timeout=10) == 0        # then clean shutdown
