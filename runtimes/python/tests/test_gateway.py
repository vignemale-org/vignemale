"""Gateway : entrée unique devant plusieurs services backend.
Auth jouée à l'edge, routage par préfixe, forward signé, identité propagée."""

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


def test_route_vers_catalog_avec_auth_edge(stack):
    status, body = get(stack, "/items/7", token="sesame")
    assert status == 200
    assert body["name"] == "widget"
    assert body["seen_user"] == "u-42"  # identité propagée à travers la gateway


def test_auth_jouee_a_l_edge_401_sans_token(stack):
    # le 401 vient de la GATEWAY — la requête n'atteint jamais le backend
    status, body = get(stack, "/items/7")
    assert status == 401 and body["code"] == "unauthenticated"


def test_chemin_inconnu_404(stack):
    status, body = get(stack, "/zzz/rien")
    assert status == 404 and body["code"] == "not_found"


def test_route_vers_orders_qui_appelle_catalog(stack):
    # gateway → orders → (call interne signé) → catalog : la chaîne complète
    status, body = get(stack, "/orders", token="sesame", method="POST", data={"item_id": 7})
    assert status == 200
    assert body["created"] is True
    assert body["item"]["name"] == "widget"
    assert body["by"] == "u-42"


def test_request_id_pose_par_la_gateway(stack):
    req = urllib.request.Request(f"http://{stack}/items/1",
                                 headers={"Authorization": "Bearer sesame"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.headers.get("x-vignemale-request-id")
