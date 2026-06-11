"""Prod-readiness : timeout, body limit, arrêt gracieux avec drain."""

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


def test_timeout_par_endpoint(hello):
    """@api(timeout=0.5) sur un handler de 3 s → 504 deadline_exceeded."""
    t0 = time.time()
    status, body = request(hello, "/slow")
    assert status == 504
    assert body["code"] == "deadline_exceeded"
    assert time.time() - t0 < 2, "la réponse doit partir au timeout, pas à la fin du handler"


def test_body_limit_par_endpoint(hello):
    """@api(body_limit=1024) → un body de 4 Kio est rejeté en 413."""
    req = urllib.request.Request(f"http://{hello}/small", data=b"x" * 4096)
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "un body trop gros doit être rejeté"
    except urllib.error.HTTPError as e:
        assert e.code == 413
        assert json.loads(e.read())["code"] == "resource_exhausted"
    # sous la limite : OK
    req = urllib.request.Request(f"http://{hello}/small", data=b'{"a":1}')
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200


@pytest.mark.skipif(sys.platform == "win32", reason="signaux POSIX")
def test_arret_gracieux_drain():
    """SIGTERM pendant une requête en vol : elle TERMINE (200), puis le
    process sort proprement — pas de requête coupée au redéploiement."""
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
    time.sleep(0.4)  # la requête est en vol
    srv.proc.send_signal(signal.SIGTERM)
    t.join(timeout=10)

    assert result.get("body") == {"done": True}, "la requête en vol doit terminer"
    assert srv.proc.wait(timeout=10) == 0
    assert "arrêté" in srv.proc.stdout.read().decode()

    # après l'arrêt : plus aucune connexion acceptée
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://{addr}/hello", timeout=2)
