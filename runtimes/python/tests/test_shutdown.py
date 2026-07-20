"""Ctrl-C (SIGINT) stops the server cleanly (regression of the blocking PyO3 join)."""

import os
import signal
import sys

import pytest

from conftest import EXAMPLES, Server, free_port


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT — POSIX only")
def test_sigint_stops_server():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [
            sys.executable,
            "-m",
            "vignemale_cli",
            "run",
            os.path.join(EXAMPLES, "shop"),
            "--addr",
            addr,
        ],
        addr,
        capture=True,
    )
    srv.proc.send_signal(signal.SIGINT)
    assert srv.proc.wait(timeout=5) == 0, "the server must exit with code 0 on Ctrl-C"
    assert "stopped" in srv.proc.stdout.read().decode()
