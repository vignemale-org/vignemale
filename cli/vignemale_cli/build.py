"""`vignemale build` : génère un Dockerfile multi-étage et construit l'image.

  - Étage 1 (builder) : compile le wheel Rust+PyO3 via maturin (protoc + cargo
    release). Le wheel est en abi3 → compatible avec n'importe quel Python ≥ 3.9.
  - Étage 2 (runtime) : `python-slim`, installe le wheel + `vignemale-cli` + le
    code de l'app. L'image démarre `vignemale run`.

Le provider switch fait que la MÊME image vise le local (MinIO/Postgres docker,
provisionnés au boot) ou la prod : `vignemale deploy` posera les variables
`VIGNEMALE_*` (DSN managé, S3 Scaleway, secrets…) et le provisioning local
devient alors un no-op.

La source vignemale (cœur Rust + SDK + CLI) est vendorée dans le contexte de
build depuis la racine du dépôt. Override possible via `VIGNEMALE_SRC`.
"""

import os
import shutil
import subprocess
import tempfile

# Parties de la source vignemale nécessaires pour compiler le wheel (le runtime
# seul : ni cli/ ni outillage dev ne partent dans l'image).
_SRC_PARTS = ["Cargo.toml", "Cargo.lock", "runtimes", "proto"]
# Exclus de la copie du contexte (lourds / inutiles au build).
_IGNORE = shutil.ignore_patterns(
    "target", ".venv", "__pycache__", "*.pyc", ".pytest_cache",
    "*.so", "dist", "*.egg-info", ".git", "vignemale_clients",
)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())


def _repo_root() -> str:
    """Racine du dépôt vignemale (workspace Cargo + runtimes/)."""
    override = os.environ.get("VIGNEMALE_SRC")
    if override:
        return os.path.abspath(override)
    # __file__ = .../cli/vignemale_cli/build.py → racine = 3 niveaux au-dessus
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if os.path.isfile(os.path.join(here, "Cargo.toml")) and os.path.isdir(
        os.path.join(here, "runtimes")
    ):
        return here
    raise SystemExit(
        "vignemale build: source vignemale introuvable (workspace Cargo + "
        "runtimes/). Pose VIGNEMALE_SRC vers la racine du dépôt vignemale."
    )


def _stage_context(app_path: str, ctx: str) -> str:
    """Remplit le contexte de build ; renvoie le chemin de l'app DANS l'image."""
    root = _repo_root()
    src = os.path.join(ctx, "src")
    os.makedirs(src)
    for part in _SRC_PARTS:
        s = os.path.join(root, part)
        d = os.path.join(src, part)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=_IGNORE)
        elif os.path.isfile(s):
            shutil.copy2(s, d)
        else:
            raise SystemExit(f"vignemale build: « {part} » manquant dans {root}")

    app_path = os.path.abspath(app_path)
    appdir = os.path.join(ctx, "app")
    if os.path.isdir(app_path):
        shutil.copytree(app_path, appdir, ignore=_IGNORE)
        return "/app"
    if os.path.isfile(app_path):
        os.makedirs(appdir)
        shutil.copy2(app_path, appdir)
        return "/app/" + os.path.basename(app_path)
    raise SystemExit(f"vignemale build: app introuvable : {app_path}")


def _dockerfile(container_app: str) -> str:
    return f"""# syntax=docker/dockerfile:1
# Généré par `vignemale build` — ne pas éditer à la main.

# ---- étage 1 : compile le wheel Rust+PyO3 (abi3, strippé+LTO) via maturin ----
FROM rust:1-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \\
        protobuf-compiler libprotobuf-dev patchelf python3 python3-dev python3-pip \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages --no-cache-dir 'maturin>=1.7,<2.0'
ENV PROTOC=/usr/bin/protoc
WORKDIR /build
COPY src/ /build/
RUN cd runtimes/python && maturin build --release --out /wheels

# ---- étage 2 : installe le RUNTIME SEUL (pydantic + .so) dans un dossier plat.
# Pas de CLI/griffe/protobuf : le runtime part en prod avec pydantic pour seule
# dépendance. python 3.11 = même version que l'image distroless finale.
FROM python:3.11-slim-bookworm AS installer
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir --target=/pylibs /tmp/*.whl \\
    && find /pylibs -type d -name __pycache__ -prune -exec rm -rf {{}} +

# ---- étage 3 : image finale distroless (python 3.11, sans shell ni pip) ----
FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=installer /pylibs /pylibs
COPY app/ /app/
# Le provider switch : en prod, `vignemale deploy` aura posé VIGNEMALE_SQLDB_* /
# VIGNEMALE_S3_* / VIGNEMALE_SECRET_* ; le point d'entrée prod ne provisionne pas.
ENV PYTHONPATH=/pylibs \\
    VIGNEMALE_ADDR=0.0.0.0:8080 \\
    VIGNEMALE_WORKERS=1
EXPOSE 8080
ENTRYPOINT ["python", "-m", "vignemale", "{container_app}"]
"""


def build(app_path: str, tag: str = None, print_only: bool = False) -> str:
    if tag is None:
        base = os.path.splitext(os.path.basename(os.path.abspath(app_path)))[0]
        tag = f"vignemale-{_sanitize(base)}:latest"

    ctx = tempfile.mkdtemp(prefix="vignemale-build-")
    keep = print_only
    try:
        container_app = _stage_context(app_path, ctx)
        dockerfile = _dockerfile(container_app)
        with open(os.path.join(ctx, "Dockerfile"), "w") as f:
            f.write(dockerfile)
        with open(os.path.join(ctx, ".dockerignore"), "w") as f:
            f.write("**/target\n**/.venv\n**/__pycache__\n**/.git\n")

        if print_only:
            print(dockerfile)
            print(f"# contexte de build prêt : {ctx}")
            print(f"# build manuel :  docker build -t {tag} {ctx}")
            return tag

        if not shutil.which("docker"):
            raise SystemExit("vignemale build: Docker requis (https://docker.com)")
        print(
            f"vignemale: build de l'image « {tag} » "
            "(compilation Rust release dans l'étage builder, patiente)…",
            flush=True,
        )
        r = subprocess.run(["docker", "build", "-t", tag, ctx])
        if r.returncode != 0:
            raise SystemExit("vignemale build: échec du docker build")
        print(
            f"vignemale: image « {tag} » prête.\n"
            f"  lancer :   docker run --rm -p 8080:8080 {tag}\n"
            f"  healthz :  curl localhost:8080/__vignemale/healthz",
            flush=True,
        )
        return tag
    finally:
        if not keep:
            shutil.rmtree(ctx, ignore_errors=True)
