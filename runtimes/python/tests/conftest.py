"""Shared test helpers: run an app in a subprocess + a minimal HTTP client."""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.abspath(os.path.join(HERE, "..", "..", "..", "examples"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """Runs a vignemale app in a subprocess and waits until it responds."""

    def __init__(self, cmd, addr, env=None, capture=False):
        self.addr = addr
        out = subprocess.PIPE if capture else subprocess.DEVNULL
        self.proc = subprocess.Popen(
            cmd, env=env or os.environ.copy(), stdout=out, stderr=out
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"the server exited during startup (code {self.proc.returncode})"
                )
            try:
                urllib.request.urlopen(f"http://{addr}/", timeout=1)
                return
            except urllib.error.HTTPError:
                return  # an HTTP response (even 404) = the server is ready
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("the server did not start within 20 s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def request(addr: str, path: str, body=None):
    """GET (or POST if `body`) → (status, decoded JSON). Does not raise on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{addr}{path}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def sse(addr: str, path: str, body=None) -> list:
    """GET/POST streaming → list of the received `data:` fragments."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{addr}{path}", data=data)
    chunks = []
    with urllib.request.urlopen(req, timeout=10) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                chunks.append(line[len("data:") :].strip())
    return chunks
