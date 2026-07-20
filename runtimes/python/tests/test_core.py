"""Python → PyO3 → Rust core pipeline: config, secrets, objects (formerly smoke.py)."""

import base64
import os

import pytest

import vignemale as v


def test_version():
    assert v.version()


def test_config_roundtrip_proto():
    b64 = v.encode_demo_config("myapp", ["site", "monitor"])
    assert v.parse_runtime_config_b64(b64) == {
        "app_id": "myapp",
        "hosted_services": ["site", "monitor"],
    }


def test_config_from_env(monkeypatch):
    monkeypatch.delenv("VIGNEMALE_RUNTIME_CONFIG", raising=False)
    assert v.load_config_from_env() is None
    monkeypatch.setenv("VIGNEMALE_RUNTIME_CONFIG", v.encode_demo_config("envapp", ["api"]))
    assert v.load_config_from_env() == {"app_id": "envapp", "hosted_services": ["api"]}


def test_secret_env(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "sk-vignemale-123")
    assert v.resolve_env_secret("MY_API_KEY") == b"sk-vignemale-123"


def test_secret_base64():
    assert v.resolve_b64_secret(base64.b64encode(b"hello").decode()) == b"hello"


def test_secret_json_key():
    assert v.resolve_json_key_secret('{"foo": "bar"}', "foo") == b"bar"


@pytest.mark.skipif(
    not os.environ.get("VIGNEMALE_TEST_S3"),
    reason="set VIGNEMALE_TEST_S3 (MinIO endpoint) to enable it",
)
def test_s3_roundtrip():
    out = v.s3_roundtrip(
        os.environ["VIGNEMALE_TEST_S3"],
        "us-east-1",
        "minioadmin",
        "minioadmin",
        "vignemale-test",
        "agents/hello.txt",
        b"world from vignemale core",
    )
    assert out == b"world from vignemale core"
