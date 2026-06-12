"""Mode multi-process (VIGNEMALE_WORKERS) : N workers partagent le port
(SO_REUSEPORT), chacun avec son propre interpréteur/GIL."""

import json
import os
import signal
import sys
import time
import urllib.request

import pytest

from conftest import EXAMPLES, Server, free_port


@pytest.mark.skipif(sys.platform == "win32", reason="fork POSIX uniquement")
def test_quatre_workers_partagent_le_port():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_WORKERS="4")
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "assistant.py"), "--addr", addr],
        addr, env=env, capture=True,
    )
    try:
        # 20 requêtes : le noyau répartit entre les 4 workers, toutes répondent
        for _ in range(20):
            with urllib.request.urlopen(f"http://{addr}/health", timeout=5) as r:
                assert json.loads(r.read()) == {"ok": True}
    finally:
        srv.proc.send_signal(signal.SIGINT)
        assert srv.proc.wait(timeout=10) == 0  # drain + arrêt propre
    out = srv.proc.stdout.read().decode()
    assert "4 workers" in out


@pytest.mark.skipif(sys.platform == "win32", reason="fork POSIX uniquement")
def test_mono_process_par_defaut():
    # sans VIGNEMALE_WORKERS, un seul process (pas de "workers" dans la sortie)
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "assistant.py"), "--addr", addr],
        addr, capture=True,
    )
    with urllib.request.urlopen(f"http://{addr}/health", timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True}
    srv.proc.send_signal(signal.SIGINT)
    srv.proc.wait(timeout=5)
    assert "endpoint(s)" in srv.proc.stdout.read().decode()
