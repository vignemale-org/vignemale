"""The `copilot` showcase example end to end: 2 folder-services,
2 databases, database-backed auth, inter-service call, streaming, isolation."""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

import pytest

from conftest import EXAMPLES, Server, free_port, sse

PG = os.environ.get("VIGNEMALE_TEST_PG")
needs_pg = pytest.mark.skipif(
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres DSN) to enable it"
)


def req(addr, path, data=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = urllib.request.Request(
        f"http://{addr}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture(scope="module")
def app():
    addr = f"127.0.0.1:{free_port()}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIGNEMALE_SQLDB")}
    env["VIGNEMALE_SQLDB"] = PG or ""  # both databases point at the test PG
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "copilot"), "--addr", addr],
        addr,
        env=env,
    )
    yield addr
    srv.stop()


@needs_pg
def test_copilot_end_to_end(app):
    email = f"ada-{uuid.uuid4().hex[:8]}@example.com"

    # signup → token ; duplicate → 409
    status, account = req(app, "/signup", {"email": email, "name": "Ada"})
    assert status == 200 and account["token"].startswith("vgm-")
    assert req(app, "/signup", {"email": email, "name": "X"})[0] == 409
    token = account["token"]

    # database-backed auth: /me OK with token, 401 without
    assert req(app, "/me", token=token) == (
        200,
        {"user_id": account["user_id"], "email": email, "name": "Ada", "plan": "free"},
    )
    assert req(app, "/me")[0] == 401

    # conversation + streamed assistant (token in query for SSE)
    status, conv = req(app, "/conversations", {"title": "Test"}, token=token)
    assert status == 200
    chunks = sse(
        app, f"/conversations/{conv['id']}/chat?token={token}", {"message": "hi"}
    )
    assert len(chunks) > 5
    reply = " ".join(chunks)
    assert "Ada" in reply  # the profile comes from the users service (inter-service call)

    # persistence: user + assistant in the database
    status, full = req(app, f"/conversations/{conv['id']}", token=token)
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
    assert full["messages"][0]["content"] == "hi"

    # isolation: another user → 403 permission_denied
    _, other = req(
        app, "/signup", {"email": f"bob-{uuid.uuid4().hex[:8]}@x.com", "name": "Bob"}
    )
    status, body = req(app, f"/conversations/{conv['id']}", token=other["token"])
    assert status == 403
    assert body["code"] == "permission_denied"
