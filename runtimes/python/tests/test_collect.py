"""Extraction statique (collect) : golden test du meta.proto + détails d'extraction."""

import json
import os

from google.protobuf import json_format

from conftest import EXAMPLES, HERE
from vignemale.collect import build_meta, extract_path

GOLDEN = os.path.join(HERE, "golden")


def meta_json(path: str) -> dict:
    extracted, app_name = extract_path(path)
    return json.loads(json_format.MessageToJson(build_meta(extracted, app_name)))


def test_shop_meta_golden():
    """Le protojson de examples/shop ne doit pas changer sans mise à jour volontaire
    du golden (tests/golden/shop_meta.json) — c'est le contrat de `vignemale check`."""
    got = meta_json(os.path.join(EXAMPLES, "shop"))
    with open(os.path.join(GOLDEN, "shop_meta.json")) as f:
        expected = json.load(f)
    assert got == expected


def test_typed_extraction_details():
    extracted, app_name = extract_path(os.path.join(EXAMPLES, "typed.py"))
    assert app_name == "typed"

    models = extracted["models"]
    assert models["ChatRequest"]["prompt"]["required"] is True
    assert models["ChatRequest"]["max_tokens"] == {
        "type": "int",
        "required": False,
        "default": 256,
    }

    (svc,) = extracted["services"]
    eps = {e["name"]: e for e in svc["endpoints"]}
    assert eps["chat"]["method"] == "POST"
    assert eps["chat"]["request"] == "ChatRequest"
    assert eps["chat"]["response"] == "ChatReply"
    assert eps["health"]["stream"] is False


def test_multi_service_extraction():
    extracted, app_name = extract_path(os.path.join(EXAMPLES, "shop"))
    assert app_name == "shop"
    assert {s["name"] for s in extracted["services"]} == {"catalog", "orders"}


def test_stream_flag_extracted():
    extracted, _ = extract_path(os.path.join(EXAMPLES, "assistant.py"))
    (svc,) = extracted["services"]
    eps = {e["name"]: e for e in svc["endpoints"]}
    assert eps["chat"]["stream"] is True
    assert eps["ask"]["stream"] is False


def test_sql_database_extracted():
    """`SQLDatabase("todo")` est détectée statiquement (c'est elle qui pilote
    le provisioning local de `vignemale run`)."""
    extracted, _ = extract_path(os.path.join(EXAMPLES, "todo.py"))
    assert extracted["databases"] == ["todo"]
    (svc,) = extracted["services"]
    assert svc["databases"] == ["todo"]


def test_sql_database_in_meta():
    got = meta_json(os.path.join(EXAMPLES, "todo.py"))
    assert got["sqlDatabases"] == [{"name": "todo"}]
    (svc,) = got["svcs"]
    assert svc["databases"] == ["todo"]
