"""Ctrl-C (SIGINT) arrête le serveur proprement (régression du join bloquant PyO3)."""

import os
import signal
import sys

import pytest

from conftest import EXAMPLES, Server, free_port


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT — POSIX uniquement")
def test_sigint_stops_server():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [
            sys.executable,
            "-m",
            "vignemale.cli",
            "run",
            os.path.join(EXAMPLES, "shop"),
            "--addr",
            addr,
        ],
        addr,
        capture=True,
    )
    srv.proc.send_signal(signal.SIGINT)
    assert srv.proc.wait(timeout=5) == 0, "le serveur doit sortir en code 0 sur Ctrl-C"
    assert "arrêté" in srv.proc.stdout.read().decode()
