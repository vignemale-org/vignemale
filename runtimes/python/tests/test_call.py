"""Appels service-à-service : local (direct) et déployé (HTTP signé),
avec propagation de l'auth et du trace-id W3C."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

from conftest import EXAMPLES, HERE, Server, free_port

SECRET = "test-secret-svc"


def post(addr, path, data, token=None):
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://{addr}{path}", data=json.dumps(data).encode(), headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# --- mode LOCAL : tous les services dans un process, call() = appel direct ---


@pytest.fixture(scope="module")
def shop():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [sys.executable, "-m", "vignemale.cli", "run", os.path.join(EXAMPLES, "shop"),
         "--addr", addr],
        addr,
    )
    yield addr
    srv.stop()


def test_call_local(shop):
    status, body = post(shop, "/orders", {"item_id": 7, "qty": 3})
    assert status == 200
    assert body == {"created": True, "item": {"id": 7, "name": "widget"}, "qty": 3}


def test_client_style_encore():
    """`from vignemale.clients import x` → client dynamique, méthodes = endpoints."""
    from vignemale.api import APIError
    from vignemale.clients import catalog  # n'importe quel nom de service

    assert repr(catalog) == "ServiceClient('catalog')"
    with pytest.raises(APIError, match="introuvable"):
        catalog.endpoint_inconnu(id=1)  # local, pas d'endpoint enregistré


def test_call_local_endpoint_inconnu(shop):
    # call() vers un endpoint inexistant → not_found propre (500 interne car
    # levé DANS le handler ? non : APIError → statut porté)
    from vignemale.api import APIError
    from vignemale.call import _call_local

    with pytest.raises(APIError, match="introuvable"):
        _call_local("catalog", "nexiste_pas", None, {})


# --- mode DÉPLOYÉ : deux process, call() = HTTP signé + propagation ---


@pytest.fixture(scope="module")
def deux_services():
    addr_cat = f"127.0.0.1:{free_port()}"
    addr_ord = f"127.0.0.1:{free_port()}"
    base = {k: v for k, v in os.environ.items()}
    env_cat = dict(
        base, VIGNEMALE_ADDR=addr_cat, VIGNEMALE_SERVICE_SECRET=SECRET,
        VIGNEMALE_SERVICE_NAME="catalog",
    )
    env_ord = dict(
        base, VIGNEMALE_ADDR=addr_ord, VIGNEMALE_SERVICE_SECRET=SECRET,
        VIGNEMALE_SERVICE_NAME="orders",
        VIGNEMALE_SERVICE_CATALOG=f"http://{addr_cat}",
    )
    cat = Server([sys.executable, os.path.join(HERE, "svc_catalog.py")], addr_cat,
                 env=env_cat, capture=True)
    ord_ = Server([sys.executable, os.path.join(HERE, "svc_orders.py")], addr_ord,
                  env=env_ord, capture=True)
    yield addr_cat, addr_ord, cat, ord_
    cat.stop()
    ord_.stop()


def test_call_http_signe_et_auth_propagee(deux_services):
    addr_cat, addr_ord, cat, ord_ = deux_services
    status, body = post(addr_ord, "/orders", {"item_id": 7}, token="sesame")
    assert status == 200
    # l'item vient de catalog, par HTTP signé, et catalog (endpoint auth=True)
    # a bien reçu les données d'auth PROPAGÉES depuis orders
    assert body["item"] == {"id": 7, "name": "widget", "seen_user": "u-42"}
    assert body["by"] == "u-42"


def test_appel_interne_non_signe_rejete(deux_services):
    addr_cat, *_ = deux_services
    status, body = post(addr_cat, "/__vignemale/call/get_item", {"params": {"id": "1"}})
    assert status == 401
    assert body["code"] == "unauthenticated"


def test_appel_interne_signature_falsifiee(deux_services):
    addr_cat, *_ = deux_services
    payload = json.dumps({"params": {"id": "1"}, "body": None}).encode()
    req = urllib.request.Request(
        f"http://{addr_cat}/__vignemale/call/get_item",
        data=payload,
        headers={
            "x-vignemale-date": str(int(time.time())),
            "x-vignemale-caller": "orders",
            "x-vignemale-signature": "deadbeef" * 8,
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False, "une signature falsifiée doit être rejetée"
    except urllib.error.HTTPError as e:
        assert e.code == 401
        assert json.loads(e.read())["message"] == "signature invalide"


def test_trace_id_propage_entre_services(deux_services):
    # EN DERNIER : ce test arrête les serveurs pour lire leurs logs.
    addr_cat, addr_ord, cat, ord_ = deux_services
    status, _ = post(addr_ord, "/orders", {"item_id": 1}, token="sesame")
    assert status == 200
    time.sleep(0.3)  # laisser les logs s'écrire
    cat.stop()
    ord_.stop()

    def trace_ids(srv, needle):
        out = []
        for line in srv.proc.stderr.read().decode().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("endpoint") == needle and rec.get("trace_id"):
                out.append(rec["trace_id"])
        return out

    orders_traces = trace_ids(ord_, "create_order")
    catalog_traces = trace_ids(cat, "get_item")
    assert orders_traces and catalog_traces
    # le même trace-id traverse les deux services (W3C traceparent)
    assert set(catalog_traces) & set(orders_traces)
