# syntax=docker/dockerfile:1
#
# Image de base Vignemale : le RUNTIME (cœur Rust + SDK Python) déjà compilé.
# Publiée en CI, multi-arch (linux/amd64 + linux/arm64), sur GHCR :
#   ghcr.io/vignemale-org/vignemale-python:latest
#
# `vignemale build <app>` part de cette image et ne fait que copier le code de
# l'app → build en quelques secondes (plus de compilation Rust côté utilisateur),
# et l'image d'app hérite du multi-arch (utile : dev sur Mac arm64, prod amd64).
#
# Construire localement (depuis la racine du dépôt) :
#   docker build -f docker/runtime.Dockerfile -t vignemale-python:latest .

# ---- étage 1 : compile le wheel Rust+PyO3 (abi3, strippé+LTO) via maturin ----
FROM rust:1-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        protobuf-compiler libprotobuf-dev patchelf python3 python3-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages --no-cache-dir 'maturin>=1.7,<2.0'
ENV PROTOC=/usr/bin/protoc
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY runtimes ./runtimes
COPY proto ./proto
RUN cd runtimes/python && maturin build --release --out /wheels

# ---- étage 2 : installe le runtime SEUL (pydantic + .so) en dossier plat ----
# python 3.11 = même version que l'image distroless finale.
FROM python:3.11-slim-bookworm AS installer
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir --target=/pylibs /tmp/*.whl \
    && find /pylibs -type d -name __pycache__ -prune -exec rm -rf {} +

# ---- étage 3 : base distroless (python 3.11, sans shell ni pip) ----
FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=installer /pylibs /pylibs
ENV PYTHONPATH=/pylibs \
    VIGNEMALE_ADDR=0.0.0.0:8080 \
    VIGNEMALE_WORKERS=1
EXPOSE 8080
# Convention : le code de l'app est monté/copié dans /app par l'image d'app.
# Une app mono-fichier surcharge l'ENTRYPOINT avec /app/<fichier>.
ENTRYPOINT ["python", "-m", "vignemale", "/app"]
