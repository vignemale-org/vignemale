"""Helpers partagés des tests : lancer une app en sous-process + client HTTP minimal."""

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
    """Lance une app vignemale en sous-process et attend qu'elle réponde."""

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
                    f"le serveur s'est arrêté au démarrage (code {self.proc.returncode})"
                )
            try:
                urllib.request.urlopen(f"http://{addr}/", timeout=1)
                return
            except urllib.error.HTTPError:
                return  # une réponse HTTP (même 404) = le serveur est prêt
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("le serveur n'a pas démarré en 20 s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def request(addr: str, path: str, body=None):
    """GET (ou POST si `body`) → (status, JSON décodé). Ne lève pas sur 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{addr}{path}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def sse(addr: str, path: str, body=None) -> list:
    """GET/POST streaming → liste des fragments `data:` reçus."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://{addr}{path}", data=data)
    chunks = []
    with urllib.request.urlopen(req, timeout=10) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                chunks.append(line[len("data:") :].strip())
    return chunks
