"""Service-to-service calls: local (direct) and deployed (signed HTTP),
with propagation of the auth data and the W3C trace-id."""

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


# --- LOCAL mode: all services in one process, call() = direct call ---


@pytest.fixture(scope="module")
def shop():
    addr = f"127.0.0.1:{free_port()}"
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run", os.path.join(EXAMPLES, "shop"),
         "--addr", addr],
        addr,
    )
    yield addr
    srv.stop()


def test_call_local(shop):
    status, body = post(shop, "/orders", {"item_id": 7, "qty": 3})
    assert status == 200
    assert body == {"created": True, "item": {"id": 7, "name": "widget"}, "qty": 3}


def test_client_encore_style():
    """`from vignemale.clients import x` → dynamic client, methods = endpoints."""
    from vignemale.api import APIError
    from vignemale.clients import catalog  # any service name

    assert repr(catalog) == "ServiceClient('catalog')"
    with pytest.raises(APIError, match="not found"):
        catalog.unknown_endpoint(id=1)  # local, no registered endpoint


def test_call_local_unknown_endpoint(shop):
    # call() to a nonexistent endpoint → clean not_found (internal 500 because
    # raised INSIDE the handler? no: APIError → status carried)
    from vignemale.api import APIError
    from vignemale.call import _call_local

    with pytest.raises(APIError, match="not found"):
        _call_local("catalog", "does_not_exist", None, {})


# --- DEPLOYED mode: two processes, call() = signed HTTP + propagation ---


@pytest.fixture(scope="module")
def two_services():
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


def test_call_http_signed_and_auth_propagated(two_services):
    addr_cat, addr_ord, cat, ord_ = two_services
    status, body = post(addr_ord, "/orders", {"item_id": 7}, token="sesame")
    assert status == 200
    # the item comes from catalog, over signed HTTP, and catalog (endpoint auth=True)
    # did receive the auth data PROPAGATED from orders
    assert body["item"] == {"id": 7, "name": "widget", "seen_user": "u-42"}
    assert body["by"] == "u-42"


def test_internal_call_unsigned_rejected(two_services):
    addr_cat, *_ = two_services
    status, body = post(addr_cat, "/__vignemale/call/get_item", {"params": {"id": "1"}})
    assert status == 401
    assert body["code"] == "unauthenticated"


def test_internal_call_forged_signature(two_services):
    addr_cat, *_ = two_services
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
        assert False, "a forged signature must be rejected"
    except urllib.error.HTTPError as e:
        assert e.code == 401
        assert json.loads(e.read())["message"] == "invalid signature"


def test_trace_id_propagated_between_services(two_services):
    # LAST: this test stops the servers to read their logs.
    addr_cat, addr_ord, cat, ord_ = two_services
    status, _ = post(addr_ord, "/orders", {"item_id": 1}, token="sesame")
    assert status == 200
    time.sleep(0.3)  # let the logs flush
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
    # the same trace-id crosses both services (W3C traceparent)
    assert set(catalog_traces) & set(orders_traces)
