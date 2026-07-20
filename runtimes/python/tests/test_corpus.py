"""The `corpus` example (enterprise RAG): indexing + pgvector embeddings,
groups, and THE product guarantee — vector search never leaves a knowledge
base the user does not have access to."""

import base64
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
    not PG, reason="set VIGNEMALE_TEST_PG (Postgres+pgvector DSN) to enable it"
)


def req(addr, path, data=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = urllib.request.Request(
        f"http://{addr}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


@pytest.fixture(scope="module")
def app():
    addr = f"127.0.0.1:{free_port()}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIGNEMALE_SQLDB")}
    env["VIGNEMALE_SQLDB"] = PG or ""
    env["VIGNEMALE_RAG_MODEL"] = "test"  # TestModel pydantic-ai (offline CI)
    srv = Server(
        [sys.executable, "-m", "vignemale_cli", "run",
         os.path.join(EXAMPLES, "corpus"), "--addr", addr],
        addr, env=env,
    )
    yield addr
    srv.stop()


@needs_pg
def test_rag_with_permissions_end_to_end(app):
    suffix = uuid.uuid4().hex[:8]

    def signup(first_name):
        _, account = req(app, "/signup",
                         {"email": f"{first_name}-{suffix}@omnes.fr", "name": first_name})
        return account

    alice, bob, carol = signup("alice"), signup("bob"), signup("carol")

    # marketing group: alice (owner) + bob
    _, grp = req(app, "/groups", {"name": f"marketing-{suffix}"}, token=alice["token"])
    s, _ = req(app, f"/groups/{grp['id']}/members",
               {"email": f"bob-{suffix}@omnes.fr"}, token=alice["token"])
    assert s == 200
    # carol cannot invite herself
    s, body = req(app, f"/groups/{grp['id']}/members",
                  {"email": f"carol-{suffix}@omnes.fr"}, token=carol["token"])
    assert (s, body["code"]) == (403, "permission_denied")

    # two KBs: one shared with the group, one private
    _, kb_pub = req(app, "/kbs", {"name": f"docs-{suffix}"}, token=alice["token"])
    _, kb_sec = req(app, "/kbs", {"name": f"secret-{suffix}"}, token=alice["token"])
    req(app, f"/kbs/{kb_pub['id']}/grant", {"group_id": grp["id"]}, token=alice["token"])

    # bob sees the shared KB, not the private one
    _, kbs_bob = req(app, "/kbs", token=bob["token"])
    assert {k["id"] for k in kbs_bob["kbs"]} == {kb_pub["id"]}

    # indexing (text → chunks → pgvector embeddings, transactional)
    doc_pub = ("Pricing for work-study programs.\n\n"
               "The work-study BTS costs 7900 euros per year in Lyon.")
    doc_sec = ("CONFIDENTIAL salary grid.\n\n"
               "The general manager earns 180000 euros per year.")
    s, r = req(app, f"/kbs/{kb_pub['id']}/documents",
               {"filename": "pricing.txt", "content_b64": b64(doc_pub)},
               token=alice["token"])
    assert s == 200 and r["chunks"] >= 1  # short paragraphs → grouped
    s, _ = req(app, f"/kbs/{kb_sec['id']}/documents",
               {"filename": "salaries.txt", "content_b64": b64(doc_sec)},
               token=alice["token"])
    assert s == 200
    # bob cannot write to the private KB
    s, body = req(app, f"/kbs/{kb_sec['id']}/documents",
                  {"filename": "x.txt", "content_b64": b64("intrusion")},
                  token=bob["token"])
    assert (s, body["code"]) == (403, "permission_denied")

    # THE guarantee: bob's search NEVER leaves his authorized KBs
    _, res = req(app, "/search", {"query": "general manager salary", "k": 5},
                 token=bob["token"])
    assert all(hit["kb_id"] == kb_pub["id"] for hit in res["results"])
    assert not any("salaries" in hit["filename"] for hit in res["results"])

    # bob finds what is shared with him
    _, res = req(app, "/search", {"query": "work-study BTS pricing", "k": 3},
                 token=bob["token"])
    assert res["results"] and res["results"][0]["filename"] == "pricing.txt"
    assert "7900" in res["results"][0]["content"]

    # alice (owner) finds the confidential doc; carol finds nothing
    _, res = req(app, "/search", {"query": "general manager salary", "k": 3},
                 token=alice["token"])
    assert res["results"][0]["filename"] == "salaries.txt"
    _, res = req(app, "/search", {"query": "work-study pricing"}, token=carol["token"])
    assert res["results"] == []

    # streamed /ask: the reply cites the authorized excerpt and its source
    chunks = sse(app, f"/ask?token={bob['token']}",
                 {"query": "how much does the work-study BTS cost?"})
    answer = " ".join(chunks)
    assert "7900" in answer and "pricing.txt" in answer


@needs_pg
def test_persisted_conversations_pydantic_ai_agent(app):
    """The Pydantic AI agent: conversations in the database, agent memory
    serialized/reloaded on each turn, persisted sources, isolation."""
    suffix = uuid.uuid4().hex[:8]
    _, fred = req(app, "/signup",
                  {"email": f"fred-{suffix}@omnes.fr", "name": "Fred"})
    token = fred["token"]

    # a KB with one document, so the RAG has sources
    _, base = req(app, "/kbs", {"name": f"hr-{suffix}"}, token=token)
    doc = "Remote-work policy.\n\n3 days per week, manager approval required."
    req(app, f"/kbs/{base['id']}/documents",
        {"filename": "remote-work.txt", "content_b64": b64(doc)}, token=token)

    s, conv = req(app, "/conversations", {"title": "HR Questions"}, token=token)
    assert s == 200

    # two streamed turns — the 2nd reloads the agent's memory from the database
    for question in ("how many days of remote work?", "and who approves?"):
        chunks = sse(app, f"/conversations/{conv['id']}/ask?token={token}",
                     {"query": question})
        assert chunks, "the reply must be streamed"

    # persistence: 2×(user+assistant), sources attached to the replies
    s, full = req(app, f"/conversations/{conv['id']}", token=token)
    assert [m["role"] for m in full["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert full["messages"][0]["content"] == "how many days of remote work?"
    assert all(m["content"] for m in full["messages"])
    sources = full["messages"][1]["sources"]
    assert sources and sources[0]["filename"] == "remote-work.txt"

    # the list shows the conversation and its message count
    _, listing = req(app, "/conversations", token=token)
    assert {"id": conv["id"], "title": "HR Questions", "messages": 4} in listing[
        "conversations"
    ]

    # isolation: another user → 403
    _, other = req(app, "/signup",
                   {"email": f"gus-{suffix}@omnes.fr", "name": "Gus"})
    s, body = req(app, f"/conversations/{conv['id']}", token=other["token"])
    assert (s, body["code"]) == (403, "permission_denied")
