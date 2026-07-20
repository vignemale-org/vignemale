# syntax=docker/dockerfile:1
#
# Vignemale base image: the RUNTIME (Rust core + Python SDK) already compiled.
# Published in CI, multi-arch (linux/amd64 + linux/arm64), to GHCR:
#   ghcr.io/vignemale-org/vignemale-python:latest
#
# `vignemale build <app>` starts from this image and only copies the app's code
# → build in a few seconds (no more Rust compilation on the user's side), and the
# app image inherits the multi-arch (useful: dev on Mac arm64, prod amd64).
#
# Build locally (from the repo root):
#   docker build -f docker/runtime.Dockerfile -t vignemale-python:latest .

# ---- stage 1: build the Rust+PyO3 wheel (abi3, stripped+LTO) via maturin ----
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

# ---- stage 2: install the runtime ONLY (pydantic + .so) into a flat folder ----
# python 3.11 = same version as the final distroless image.
FROM python:3.11-slim-bookworm AS installer
COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir --target=/pylibs /tmp/*.whl \
    && find /pylibs -type d -name __pycache__ -prune -exec rm -rf {} +

# ---- stage 3: distroless base (python 3.11, no shell or pip) ----
FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=installer /pylibs /pylibs
ENV PYTHONPATH=/pylibs \
    VIGNEMALE_ADDR=0.0.0.0:8080 \
    VIGNEMALE_WORKERS=1
EXPOSE 8080
# Convention: the app's code is mounted/copied into /app by the app image.
# A single-file app overrides the ENTRYPOINT with /app/<file>.
ENTRYPOINT ["python", "-m", "vignemale", "/app"]
