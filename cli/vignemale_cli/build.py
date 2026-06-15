"""`vignemale build` : construit l'image Docker de l'app.

Deux chemins :

  - **rapide (défaut)** : `FROM <image de base>` + copie du code de l'app. L'image
    de base `vignemale-python` contient le runtime (cœur Rust + SDK) DÉJÀ compilé
    et publié en CI — le build d'une app prend quelques secondes, pas de Rust.
  - **--from-source** : compile le wheel Rust+PyO3 dans un étage builder (lent),
    utile en dev quand le cœur n'est pas encore publié, ou hors ligne.

Dans les deux cas l'image finale est distroless (python 3.11, sans shell ni pip)
et n'embarque QUE le runtime (pydantic + le .so) : le runtime part en prod avec
pydantic pour seule dépendance.

Le provider switch fait que la même image vise le local ou la prod : `vignemale
deploy` posera les `VIGNEMALE_*` (DSN managé, S3 Scaleway, secrets…) ; le point
d'entrée prod (`python -m vignemale`) ne provisionne rien.
"""

import os
import shutil
import subprocess
import tempfile

# Image de base par défaut (runtime pré-compilé, publiée en CI sur GHCR).
# Surchargeable via --base ou VIGNEMALE_BASE_IMAGE.
DEFAULT_BASE_IMAGE = os.environ.get(
    "VIGNEMALE_BASE_IMAGE", "ghcr.io/jacqkues/vignemale-python:latest"
)

# Parties de la source vignemale nécessaires pour compiler le wheel (--from-source).
_SRC_PARTS = ["Cargo.toml", "Cargo.lock", "runtimes", "proto"]
# Exclus de la copie du contexte (lourds / inutiles au build).
_IGNORE = shutil.ignore_patterns(
    "target", ".venv", "__pycache__", "*.pyc", ".pytest_cache",
    "*.so", "dist", "*.egg-info", ".git", "vignemale_clients",
)


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())


def _repo_root() -> str:
    """Racine du dépôt vignemale (workspace Cargo + runtimes/) — pour --from-source."""
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
        "vignemale build --from-source: source vignemale introuvable (workspace "
        "Cargo + runtimes/). Pose VIGNEMALE_SRC vers la racine du dépôt vignemale."
    )


def _stage_app(app_path: str, ctx: str) -> str:
    """Copie le code de l'app dans le contexte ; renvoie son chemin DANS l'image."""
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


def _stage_src(ctx: str) -> None:
    """Copie la source vignemale dans le contexte (pour --from-source)."""
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


def _dockerfile_fast(container_app: str, base: str) -> str:
    return f"""# syntax=docker/dockerfile:1
# Généré par `vignemale build` — app au-dessus du runtime pré-compilé.
FROM {base}
COPY app/ /app/
ENTRYPOINT ["python", "-m", "vignemale", "{container_app}"]
"""


def _dockerfile_source(container_app: str) -> str:
    return f"""# syntax=docker/dockerfile:1
# Généré par `vignemale build --from-source` — compile le runtime puis l'app.

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

# ---- étage 2 : installe le runtime SEUL (pydantic + .so) en dossier plat ----
FROM python:3.11-slim-bookworm AS installer
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir --target=/pylibs /tmp/*.whl \\
    && find /pylibs -type d -name __pycache__ -prune -exec rm -rf {{}} +

# ---- étage 3 : image finale distroless (python 3.11, sans shell ni pip) ----
FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=installer /pylibs /pylibs
COPY app/ /app/
ENV PYTHONPATH=/pylibs \\
    VIGNEMALE_ADDR=0.0.0.0:8080 \\
    VIGNEMALE_WORKERS=1
EXPOSE 8080
ENTRYPOINT ["python", "-m", "vignemale", "{container_app}"]
"""


def build(
    app_path: str,
    tag: str = None,
    print_only: bool = False,
    from_source: bool = False,
    base: str = None,
    platform: str = None,
    push: bool = False,
) -> str:
    base = base or DEFAULT_BASE_IMAGE
    if tag is None:
        name = os.path.splitext(os.path.basename(os.path.abspath(app_path)))[0]
        tag = f"vignemale-{_sanitize(name)}:latest"

    ctx = tempfile.mkdtemp(prefix="vignemale-build-")
    keep = print_only
    try:
        container_app = _stage_app(app_path, ctx)
        if from_source:
            _stage_src(ctx)
            dockerfile = _dockerfile_source(container_app)
        else:
            dockerfile = _dockerfile_fast(container_app, base)
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

        # buildx dès qu'on cible une plateforme (ex. linux/amd64 pour Scaleway
        # depuis un Mac arm64) ou qu'on pousse au registry.
        if platform or push:
            cmd = ["docker", "buildx", "build"]
            if platform:
                cmd += ["--platform", platform]
            cmd += ["-t", tag, "--push" if push else "--load", ctx]
        else:
            cmd = ["docker", "build", "-t", tag, ctx]

        target = "pousse" if push else "build"
        where = f" ({platform})" if platform else ""
        src = "compilation Rust release, patiente" if from_source else f"au-dessus de {base}"
        print(f"vignemale: {target} de l'image « {tag} »{where} ({src})…", flush=True)
        r = subprocess.run(cmd)
        if r.returncode != 0:
            hint = ""
            if not from_source:
                hint = (
                    f"\n  (l'image de base {base} est-elle accessible ? "
                    "`docker login ghcr.io`, ou `--from-source`)"
                )
            raise SystemExit(f"vignemale build: échec du build{hint}")
        if push:
            print(f"vignemale: image « {tag} » poussée. Déploie :\n"
                  f"  vignemale deploy <app> --image {tag}", flush=True)
        else:
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
