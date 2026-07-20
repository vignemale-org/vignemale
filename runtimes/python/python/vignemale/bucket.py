"""`Bucket` primitive: S3-compatible Object Storage, served by the Rust core.

    from vignemale import Bucket

    docs = Bucket("documents")
    docs.put("report.pdf", pdf_bytes)
    data = docs.get("report.pdf")
    for key in docs.list(prefix="2026/"):
        ...

Provider switch (Encore style): the code declares the bucket, the ENVIRONMENT
provides the S3 backend. Locally: MinIO (auto-provisioned by `vignemale run`).
In prod: Scaleway Object Storage (or any S3-compatible backend).

Config resolved from the environment:
  - `VIGNEMALE_S3_ENDPOINT` (e.g. http://127.0.0.1:9100 locally)
  - `VIGNEMALE_S3_REGION`        (default: us-east-1)
  - `VIGNEMALE_S3_ACCESS_KEY` / `VIGNEMALE_S3_SECRET_KEY`
  - cloud name of the bucket: `VIGNEMALE_BUCKET_<NAME>` (default: the logical name)
"""

import os

from . import _core

# Declared buckets (for collect / meta + local provisioning).
_buckets: list = []


class BucketError(Exception):
    """Object Storage error (connection, missing key…) — message from the core."""


class Bucket:
    def __init__(self, name: str):
        self.name = name
        _buckets.append(self)

    @property
    def cloud_name(self) -> str:
        return os.environ.get(
            f"VIGNEMALE_BUCKET_{self.name.upper().replace('-', '_')}", self.name
        )

    def _cfg(self) -> tuple:
        endpoint = os.environ.get("VIGNEMALE_S3_ENDPOINT")
        if not endpoint:
            raise BucketError(
                f"no S3 backend for bucket '{self.name}': set "
                "VIGNEMALE_S3_ENDPOINT (+ ACCESS_KEY / SECRET_KEY), or launch via "
                "`vignemale run` which provisions MinIO locally"
            )
        return (
            endpoint,
            os.environ.get("VIGNEMALE_S3_REGION", "us-east-1"),
            os.environ.get("VIGNEMALE_S3_ACCESS_KEY", ""),
            os.environ.get("VIGNEMALE_S3_SECRET_KEY", ""),
            self.cloud_name,
        )

    def _op(self, op: str, key: str = "", value: bytes = None):
        try:
            return _core.bucket_op(self._cfg(), op, key, value)
        except RuntimeError as e:
            raise BucketError(str(e)) from None

    def create_if_not_exists(self) -> None:
        self._op("create")

    def put(self, key: str, data: bytes) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._op("put", key, data)

    def get(self, key: str) -> bytes:
        return self._op("get", key)

    def exists(self, key: str) -> bool:
        return self._op("exists", key)

    def list(self, prefix: str = "") -> list:
        return self._op("list", prefix)

    def delete(self, key: str) -> None:
        self._op("delete", key)

    def __repr__(self) -> str:
        return f"Bucket({self.name!r})"
