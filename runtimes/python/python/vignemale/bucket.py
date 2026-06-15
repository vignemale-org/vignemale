"""Primitive `Bucket` : Object Storage S3-compatible, servie par le core Rust.

    from vignemale import Bucket

    docs = Bucket("documents")
    docs.put("rapport.pdf", pdf_bytes)
    data = docs.get("rapport.pdf")
    for key in docs.list(prefix="2026/"):
        ...

Provider switch (façon Encore) : le code déclare le bucket, l'ENVIRONNEMENT
fournit le backend S3. En local : MinIO (auto-provisionné par `vignemale run`).
En prod : Scaleway Object Storage (ou tout S3-compatible).

Config résolue depuis l'environnement :
  - `VIGNEMALE_S3_ENDPOINT` (ex. http://127.0.0.1:9100 en local)
  - `VIGNEMALE_S3_REGION`        (défaut: us-east-1)
  - `VIGNEMALE_S3_ACCESS_KEY` / `VIGNEMALE_S3_SECRET_KEY`
  - nom cloud du bucket : `VIGNEMALE_BUCKET_<NOM>` (défaut: le nom logique)
"""

import os

from . import _core

# Buckets déclarés (pour collect / meta + provisioning local).
_buckets: list = []


class BucketError(Exception):
    """Erreur Object Storage (connexion, clé absente…) — message du core."""


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
                f"aucun backend S3 pour le bucket '{self.name}' : pose "
                "VIGNEMALE_S3_ENDPOINT (+ ACCESS_KEY / SECRET_KEY), ou lance via "
                "`vignemale run` qui provisionne MinIO en local"
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
