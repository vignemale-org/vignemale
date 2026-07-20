"""Multi-process mode (VIGNEMALE_WORKERS): N workers share the port
(SO_REUSEPORT), each with its own interpreter/GIL."""

import json
import os
import signal
import sys
import time
import urllib.request

import pytest

from conftest import EXAMPLES, Server, free_port


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork only")
def test_four_workers_share_the_port():
    addr = f"127.0.0.1:{free_port()}"
    env = dict(os.environ, VIGNEMALE_WORKERS="4")
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "assistant.py"), "--addr", addr],
        addr, env=env, capture=True,
    )
    try:
        # 20 requests: the kernel spreads them across the 4 workers, all respond
        for _ in range(20):
            with urllib.request.urlopen(f"http://{addr}/health", timeout=5) as r:
                assert json.loads(r.read()) == {"ok": True}
    finally:
        srv.proc.send_signal(signal.SIGINT)
        assert srv.proc.wait(timeout=10) == 0  # drain + clean shutdown
    out = srv.proc.stdout.read().decode()
    assert "4 workers" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork only")
def test_mono_process_by_default():
    # without VIGNEMALE_WORKERS, a single process (no "workers" in the output)
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
