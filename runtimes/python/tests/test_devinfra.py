"""Provisioning local zéro-config : `vignemale run` sur une app qui déclare
une `SQLDatabase` démarre le Postgres Docker partagé et pose le DSN tout seul."""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from conftest import EXAMPLES, free_port


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    return r.returncode == 0


needs_docker = pytest.mark.skipif(
    not docker_ready(), reason="docker indisponible (requis pour le provisioning local)"
)


@needs_docker
def test_run_provisions_database_automatically():
    addr = f"127.0.0.1:{free_port()}"
    # environnement vierge : aucun DSN — c'est Vignemale qui doit le poser
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIGNEMALE_SQLDB")}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vignemale.cli",
            "run",
            os.path.join(EXAMPLES, "todo.py"),
            "--addr",
            addr,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        # large timeout : premier lancement = pull de l'image + init du cluster
        deadline = time.time() + 180
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"`vignemale run` s'est arrêté (code {proc.returncode})")
            try:
                urllib.request.urlopen(f"http://{addr}/todos", timeout=1)
                ready = True
                break
            except urllib.error.HTTPError:
                ready = True
                break
            except Exception:
                time.sleep(0.5)
        assert ready, "le serveur n'a pas démarré (provisioning compris)"

        req = urllib.request.Request(
            f"http://{addr}/todos", data=json.dumps({"title": "auto"}).encode()
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            created = json.loads(r.read())
        assert created["title"] == "auto"
        assert isinstance(created["id"], int)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
