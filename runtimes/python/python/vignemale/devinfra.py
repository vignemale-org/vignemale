"""Provisioning local (dev) : `vignemale run` fait apparaître l'infra déclarée.

Le code déclare `SQLDatabase("todo")` ; au `run`, Vignemale démarre (ou
réutilise) un Postgres Docker partagé, crée la base si besoin et pose le DSN
dans l'environnement — rien à installer, rien à exporter, façon Encore.

Un seul conteneur (`vignemale-postgres`, port 5498, volume persistant) sert
toutes les apps ; chaque `SQLDatabase("x")` devient une database `x` dedans.
Un DSN déjà présent dans l'environnement (VIGNEMALE_SQLDB_<NOM> ou
VIGNEMALE_SQLDB) a priorité : c'est le provider switch — même code, autre
backend.
"""

import json
import os
import platform
import shutil
import subprocess
import time

from . import _core

CONTAINER = "vignemale-postgres"
VOLUME = "vignemale-pg-data"
PORT = 5498
# pgvector inclus (RAG/embeddings) — même Postgres 16, extension en plus,
# comme les Managed Database Scaleway.
IMAGE = "pgvector/pgvector:pg16"
_PASSWORD = "vignemale"  # dev local uniquement
ADMIN_DSN = f"postgres://postgres:{_PASSWORD}@127.0.0.1:{PORT}/postgres"


def provision_local(db_names: list) -> None:
    """Pose un DSN dans l'env pour chaque base déclarée qui n'en a pas déjà un."""
    missing = []
    for name in db_names:
        env_key = _env_key(name)
        if not (os.environ.get(env_key) or os.environ.get("VIGNEMALE_SQLDB")):
            missing.append((name, env_key))
    if not missing:
        return

    _ensure_postgres()
    for name, env_key in missing:
        dbname = _sanitize(name)
        _ensure_database(dbname)
        os.environ[env_key] = (
            f"postgres://postgres:{_PASSWORD}@127.0.0.1:{PORT}/{dbname}"
        )
        print(f"vignemale: base Postgres « {name} » prête (docker local)", flush=True)


def _env_key(name: str) -> str:
    return f"VIGNEMALE_SQLDB_{name.upper().replace('-', '_')}"


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower())


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _ensure_postgres() -> None:
    if not shutil.which("docker"):
        raise SystemExit(
            "vignemale: l'app déclare une base SQL ; il faut Docker pour le "
            "Postgres local (https://docker.com) — ou pose VIGNEMALE_SQLDB toi-même"
        )
    if _docker("info").returncode != 0:
        _start_docker_daemon()

    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if state.returncode != 0:
        print("vignemale: démarrage du Postgres local (docker)…", flush=True)
        r = _docker(
            "run", "-d",
            "--name", CONTAINER,
            "-p", f"{PORT}:5432",
            "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
            "-v", f"{VOLUME}:/var/lib/postgresql/data",
            IMAGE,
        )
        if r.returncode != 0:
            raise SystemExit(
                f"vignemale: impossible de lancer le Postgres local: {r.stderr.strip()}"
            )
    elif state.stdout.strip() != "true":
        _docker("start", CONTAINER)

    _wait_ready()


def _start_docker_daemon() -> None:
    if platform.system() == "Darwin":
        print("vignemale: démarrage de Docker…", flush=True)
        subprocess.run(["open", "-a", "Docker"], capture_output=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if _docker("info").returncode == 0:
                return
            time.sleep(2)
    raise SystemExit("vignemale: le démon Docker ne répond pas — lance Docker puis réessaie")


def _wait_ready(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _core.sqldb_query(ADMIN_DSN, "SELECT 1", "[]")
            return
        except RuntimeError:
            time.sleep(0.4)
    raise SystemExit("vignemale: le Postgres local n'a pas démarré à temps")


def _ensure_database(dbname: str) -> None:
    rows = json.loads(
        _core.sqldb_query(
            ADMIN_DSN, "SELECT 1 FROM pg_database WHERE datname = $1", json.dumps([dbname])
        )
    )
    if not rows:
        _core.sqldb_execute(ADMIN_DSN, f'CREATE DATABASE "{dbname}"', "[]")
