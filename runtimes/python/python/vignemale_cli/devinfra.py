"""Local provisioning (dev): `vignemale run` brings up the declared infra.

The code declares `SQLDatabase("todo")`; at `run`, Vignemale starts (or
reuses) a shared Docker Postgres, creates the database if needed and sets the DSN
in the environment — nothing to install, nothing to export, Encore-style.

A single container (`vignemale-postgres`, port 5498, persistent volume) serves
all the apps; each `SQLDatabase("x")` becomes a database `x` inside it.
A DSN already present in the environment (VIGNEMALE_SQLDB_<NAME> or
VIGNEMALE_SQLDB) takes priority: that's the provider switch — same code, other
backend.
"""

import json
import os
import platform
import shutil
import subprocess
import time

from vignemale import _core

CONTAINER = "vignemale-postgres"
VOLUME = "vignemale-pg-data"
PORT = 5498
# pgvector included (RAG/embeddings) — same Postgres 16, extra extension,
# like Scaleway Managed Databases.
IMAGE = "pgvector/pgvector:pg16"
_PASSWORD = "vignemale"  # local dev only
ADMIN_DSN = f"postgres://postgres:{_PASSWORD}@127.0.0.1:{PORT}/postgres"


def provision_local(db_names: list) -> None:
    """Sets a DSN in the env for each declared database that does not already have one."""
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
        print(f'vignemale: Postgres database "{name}" ready (local docker)', flush=True)


def _env_key(name: str) -> str:
    return f"VIGNEMALE_SQLDB_{name.upper().replace('-', '_')}"


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower())


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _ensure_postgres() -> None:
    if not shutil.which("docker"):
        raise SystemExit(
            "vignemale: the app declares a SQL database; Docker is needed for the "
            "local Postgres (https://docker.com) — or set VIGNEMALE_SQLDB yourself"
        )
    if _docker("info").returncode != 0:
        _start_docker_daemon()

    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if state.returncode != 0:
        print("vignemale: starting the local Postgres (docker)…", flush=True)
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
                f"vignemale: unable to start the local Postgres: {r.stderr.strip()}"
            )
    elif state.stdout.strip() != "true":
        _docker("start", CONTAINER)

    _wait_ready()


def _start_docker_daemon() -> None:
    if platform.system() == "Darwin":
        print("vignemale: starting Docker…", flush=True)
        subprocess.run(["open", "-a", "Docker"], capture_output=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if _docker("info").returncode == 0:
                return
            time.sleep(2)
    raise SystemExit("vignemale: the Docker daemon is not responding — start Docker then retry")


def _wait_ready(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _core.sqldb_query(ADMIN_DSN, "SELECT 1", "[]")
            return
        except RuntimeError:
            time.sleep(0.4)
    raise SystemExit("vignemale: the local Postgres did not start in time")


def _ensure_database(dbname: str) -> None:
    rows = json.loads(
        _core.sqldb_query(
            ADMIN_DSN, "SELECT 1 FROM pg_database WHERE datname = $1", json.dumps([dbname])
        )
    )
    if not rows:
        _core.sqldb_execute(ADMIN_DSN, f'CREATE DATABASE "{dbname}"', "[]")


# --- Local Object Storage: MinIO (S3-compatible), like Postgres above ---

MINIO_CONTAINER = "vignemale-minio"
MINIO_VOLUME = "vignemale-minio-data"
MINIO_PORT = 9100
MINIO_KEY = "minioadmin"  # local dev only
MINIO_ENDPOINT = f"http://127.0.0.1:{MINIO_PORT}"


def provision_buckets(bucket_names: list) -> None:
    """Starts MinIO locally (if needed), creates the buckets, sets the S3 config.

    Skipped if `VIGNEMALE_S3_ENDPOINT` is already set (prod / provider switch)."""
    if not bucket_names or os.environ.get("VIGNEMALE_S3_ENDPOINT"):
        return
    _ensure_minio()
    os.environ.setdefault("VIGNEMALE_S3_ENDPOINT", MINIO_ENDPOINT)
    os.environ.setdefault("VIGNEMALE_S3_REGION", "us-east-1")
    os.environ.setdefault("VIGNEMALE_S3_ACCESS_KEY", MINIO_KEY)
    os.environ.setdefault("VIGNEMALE_S3_SECRET_KEY", MINIO_KEY)
    for name in bucket_names:
        cloud = _sanitize(name)
        _core.bucket_op(
            (MINIO_ENDPOINT, "us-east-1", MINIO_KEY, MINIO_KEY, cloud), "create"
        )
        print(f'vignemale: S3 bucket "{name}" ready (local minio)', flush=True)


def _ensure_minio() -> None:
    if not shutil.which("docker"):
        raise SystemExit(
            "vignemale: the app declares a Bucket; Docker is needed for MinIO "
            "locally — or set VIGNEMALE_S3_ENDPOINT yourself"
        )
    if _docker("info").returncode != 0:
        _start_docker_daemon()
    state = _docker("inspect", "-f", "{{.State.Running}}", MINIO_CONTAINER)
    if state.returncode != 0:
        print("vignemale: starting local MinIO (docker)…", flush=True)
        r = _docker(
            "run", "-d",
            "--name", MINIO_CONTAINER,
            "-p", f"{MINIO_PORT}:9000",
            "-e", f"MINIO_ROOT_USER={MINIO_KEY}",
            "-e", f"MINIO_ROOT_PASSWORD={MINIO_KEY}",
            "-v", f"{MINIO_VOLUME}:/data",
            "minio/minio", "server", "/data",
        )
        if r.returncode != 0:
            raise SystemExit(f"vignemale: unable to start MinIO: {r.stderr.strip()}")
    elif state.stdout.strip() != "true":
        _docker("start", MINIO_CONTAINER)
    _wait_minio()


def _wait_minio(timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _core.bucket_op(
                (MINIO_ENDPOINT, "us-east-1", MINIO_KEY, MINIO_KEY, "vignemale-probe"),
                "create",
            )
            return
        except RuntimeError:
            time.sleep(0.4)
    raise SystemExit("vignemale: MinIO did not start in time")
