"""Gateway: single entry point in front of several backend services.
Auth run at the edge, prefix routing, signed forwarding, propagated identity."""

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

from conftest import EXAMPLES, HERE, Server, free_port

SECRET = "gw-test-secret"


def get(addr, path, token=None, method=None, data=None):
    headers = {"content-type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"http://{addr}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(scope="module")
def stack():
    a_cat, a_ord, a_gw = free_port(), free_port(), free_port()
    base = dict(os.environ, VIGNEMALE_SERVICE_SECRET=SECRET)
    cat = Server(
        [sys.executable, os.path.join(HERE, "svc_catalog.py")], f"127.0.0.1:{a_cat}",
        env=dict(base, VIGNEMALE_ADDR=f"127.0.0.1:{a_cat}", VIGNEMALE_SERVICE_NAME="catalog"),
    )
    ordp = Server(
        [sys.executable, os.path.join(HERE, "svc_orders.py")], f"127.0.0.1:{a_ord}",
        env=dict(base, VIGNEMALE_ADDR=f"127.0.0.1:{a_ord}", VIGNEMALE_SERVICE_NAME="orders",
                 VIGNEMALE_SERVICE_CATALOG=f"http://127.0.0.1:{a_cat}"),
    )
    gw = Server(
        [sys.executable, "-m", "vignemale_cli", "gateway",
         os.path.join(EXAMPLES, "shop"), "--addr", f"127.0.0.1:{a_gw}"],
        f"127.0.0.1:{a_gw}",
        env=dict(base, VIGNEMALE_SERVICE_CATALOG=f"http://127.0.0.1:{a_cat}",
                 VIGNEMALE_SERVICE_ORDERS=f"http://127.0.0.1:{a_ord}"),
    )
    yield f"127.0.0.1:{a_gw}"
    gw.stop(); ordp.stop(); cat.stop()


def test_route_to_catalog_with_edge_auth(stack):
    status, body = get(stack, "/items/7", token="sesame")
    assert status == 200
    assert body["name"] == "widget"
    assert body["seen_user"] == "u-42"  # identity propagated through the gateway


def test_auth_run_at_edge_401_without_token(stack):
    # the 401 comes from the GATEWAY — the request never reaches the backend
    status, body = get(stack, "/items/7")
    assert status == 401 and body["code"] == "unauthenticated"


def test_unknown_path_404(stack):
    status, body = get(stack, "/zzz/nothing")
    assert status == 404 and body["code"] == "not_found"


def test_route_to_orders_that_calls_catalog(stack):
    # gateway → orders → (signed internal call) → catalog: the full chain
    status, body = get(stack, "/orders", token="sesame", method="POST", data={"item_id": 7})
    assert status == 200
    assert body["created"] is True
    assert body["item"]["name"] == "widget"
    assert body["by"] == "u-42"


def test_request_id_set_by_the_gateway(stack):
    req = urllib.request.Request(f"http://{stack}/items/1",
                                 headers={"Authorization": "Bearer sesame"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.headers.get("x-vignemale-request-id")
