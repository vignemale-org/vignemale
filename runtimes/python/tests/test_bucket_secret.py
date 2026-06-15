"""Primitives Bucket (S3 via le core, MinIO auto-provisionné) et Secret."""

import json
import os
import sys
import urllib.request

import pytest

from conftest import HERE, Server, free_port


# --- Secret : unitaire, pas besoin d'infra ---

def test_secret_resolution(monkeypatch):
    from vignemale import Secret

    monkeypatch.setenv("VIGNEMALE_SECRET_OPENAI_API_KEY", "via-prefix")
    assert Secret("OPENAI_API_KEY").get() == "via-prefix"
    monkeypatch.delenv("VIGNEMALE_SECRET_OPENAI_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "via-brut")
    assert Secret("OPENAI_API_KEY").get() == "via-brut"


def test_secret_absent_explique(monkeypatch):
    from vignemale import Secret

    monkeypatch.delenv("ABSENT_XYZ", raising=False)
    monkeypatch.delenv("VIGNEMALE_SECRET_ABSENT_XYZ", raising=False)
    with pytest.raises(KeyError, match="ABSENT_XYZ"):
        Secret("ABSENT_XYZ").get()


def test_secret_et_bucket_declares_dans_collect(tmp_path):
    app = tmp_path / "app.py"
    app.write_text(
        "from vignemale import Bucket, Secret, api\n"
        "files = Bucket('uploads')\n"
        "KEY = Secret('MY_KEY')\n"
        "@api(method='GET', path='/x')\n"
        "def x() -> dict: return {}\n"
    )
    from vignemale_cli.collect import extract_path

    extracted, _ = extract_path(str(app))
    assert extracted["buckets"] == ["uploads"]
    assert extracted["secrets"] == ["MY_KEY"]


# --- Bucket : intégration MinIO (skip si MinIO indisponible) ---

def _minio_up():
    import shutil
    import subprocess
    if not shutil.which("docker"):
        return False
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "vignemale-minio"],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


needs_minio = pytest.mark.skipif(
    not _minio_up(), reason="conteneur vignemale-minio non démarré"
)


@needs_minio
def test_bucket_put_get_list_delete():
    from vignemale import Bucket

    os.environ.setdefault("VIGNEMALE_S3_ENDPOINT", "http://127.0.0.1:9100")
    os.environ.setdefault("VIGNEMALE_S3_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("VIGNEMALE_S3_SECRET_KEY", "minioadmin")
    import uuid

    b = Bucket(f"pytest-{uuid.uuid4().hex[:8]}")
    b.create_if_not_exists()
    assert b.exists("k.txt") is False
    b.put("k.txt", b"bonjour")
    assert b.exists("k.txt") is True
    assert b.get("k.txt") == b"bonjour"
    assert "k.txt" in b.list()
    b.delete("k.txt")
    assert b.exists("k.txt") is False
