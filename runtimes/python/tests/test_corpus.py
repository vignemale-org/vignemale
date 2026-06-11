"""L'exemple `corpus` (RAG d'entreprise) : indexation + embeddings pgvector,
groupes, et LA garantie produit — la recherche vectorielle ne sort jamais
d'une knowledge base à laquelle l'utilisateur n'a pas accès."""

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
    not PG, reason="pose VIGNEMALE_TEST_PG (DSN Postgres+pgvector) pour l'activer"
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
    env["VIGNEMALE_RAG_MODEL"] = "test"  # TestModel pydantic-ai (CI hors-ligne)
    srv = Server(
        [sys.executable, "-m", "vignemale.cli", "run",
         os.path.join(EXAMPLES, "corpus"), "--addr", addr],
        addr, env=env,
    )
    yield addr
    srv.stop()


@needs_pg
def test_rag_avec_permissions_de_bout_en_bout(app):
    suffixe = uuid.uuid4().hex[:8]

    def signup(prenom):
        _, compte = req(app, "/signup",
                        {"email": f"{prenom}-{suffixe}@omnes.fr", "name": prenom})
        return compte

    alice, bob, carol = signup("alice"), signup("bob"), signup("carol")

    # groupe marketing : alice (owner) + bob
    _, grp = req(app, "/groups", {"name": f"marketing-{suffixe}"}, token=alice["token"])
    s, _ = req(app, f"/groups/{grp['id']}/members",
               {"email": f"bob-{suffixe}@omnes.fr"}, token=alice["token"])
    assert s == 200
    # carol ne peut pas s'inviter
    s, body = req(app, f"/groups/{grp['id']}/members",
                  {"email": f"carol-{suffixe}@omnes.fr"}, token=carol["token"])
    assert (s, body["code"]) == (403, "permission_denied")

    # deux KB : une partagée au groupe, une privée
    _, kb_pub = req(app, "/kbs", {"name": f"docs-{suffixe}"}, token=alice["token"])
    _, kb_sec = req(app, "/kbs", {"name": f"secret-{suffixe}"}, token=alice["token"])
    req(app, f"/kbs/{kb_pub['id']}/grant", {"group_id": grp["id"]}, token=alice["token"])

    # bob voit la KB partagée, pas la privée
    _, kbs_bob = req(app, "/kbs", token=bob["token"])
    assert {k["id"] for k in kbs_bob["kbs"]} == {kb_pub["id"]}

    # indexation (texte → chunks → embeddings pgvector, transactionnel)
    doc_pub = ("Tarifs des formations en alternance.\n\n"
               "Le BTS en alternance coûte 7900 euros par an à Lyon.")
    doc_sec = ("CONFIDENTIEL grille des salaires.\n\n"
               "Le directeur général perçoit 180000 euros annuels.")
    s, r = req(app, f"/kbs/{kb_pub['id']}/documents",
               {"filename": "tarifs.txt", "content_b64": b64(doc_pub)},
               token=alice["token"])
    assert s == 200 and r["chunks"] >= 1  # paragraphes courts → regroupés
    s, _ = req(app, f"/kbs/{kb_sec['id']}/documents",
               {"filename": "salaires.txt", "content_b64": b64(doc_sec)},
               token=alice["token"])
    assert s == 200
    # bob ne peut pas écrire dans la KB privée
    s, body = req(app, f"/kbs/{kb_sec['id']}/documents",
                  {"filename": "x.txt", "content_b64": b64("intrusion")},
                  token=bob["token"])
    assert (s, body["code"]) == (403, "permission_denied")

    # LA garantie : la recherche de bob ne sort JAMAIS de ses KB autorisées
    _, res = req(app, "/search", {"query": "salaires directeur général", "k": 5},
                 token=bob["token"])
    assert all(hit["kb_id"] == kb_pub["id"] for hit in res["results"])
    assert not any("salaires" in hit["filename"] for hit in res["results"])

    # bob trouve ce qui lui est partagé
    _, res = req(app, "/search", {"query": "tarif BTS alternance", "k": 3},
                 token=bob["token"])
    assert res["results"] and res["results"][0]["filename"] == "tarifs.txt"
    assert "7900" in res["results"][0]["content"]

    # alice (propriétaire) trouve le confidentiel ; carol ne trouve rien
    _, res = req(app, "/search", {"query": "salaires directeur général", "k": 3},
                 token=alice["token"])
    assert res["results"][0]["filename"] == "salaires.txt"
    _, res = req(app, "/search", {"query": "tarif alternance"}, token=carol["token"])
    assert res["results"] == []

    # /ask streamé : la réponse cite l'extrait autorisé et sa source
    chunks = sse(app, f"/ask?token={bob['token']}",
                 {"query": "combien coûte le BTS en alternance ?"})
    reponse = " ".join(chunks)
    assert "7900" in reponse and "tarifs.txt" in reponse


@needs_pg
def test_conversations_persistees_agent_pydantic_ai(app):
    """L'agent Pydantic AI : conversations en base, mémoire de l'agent
    sérialisée/rechargée à chaque tour, sources persistées, isolation."""
    suffixe = uuid.uuid4().hex[:8]
    _, fred = req(app, "/signup",
                  {"email": f"fred-{suffixe}@omnes.fr", "name": "Fred"})
    token = fred["token"]

    # une KB avec un document, pour que le RAG ait des sources
    _, base = req(app, "/kbs", {"name": f"rh-{suffixe}"}, token=token)
    doc = "Politique de télétravail.\n\n3 jours par semaine, accord du manager requis."
    req(app, f"/kbs/{base['id']}/documents",
        {"filename": "teletravail.txt", "content_b64": b64(doc)}, token=token)

    s, conv = req(app, "/conversations", {"title": "Questions RH"}, token=token)
    assert s == 200

    # deux tours streamés — le 2e recharge la mémoire de l'agent depuis la base
    for question in ("combien de jours de télétravail ?", "et qui valide ?"):
        chunks = sse(app, f"/conversations/{conv['id']}/ask?token={token}",
                     {"query": question})
        assert chunks, "la réponse doit être streamée"

    # persistance : 2×(user+assistant), sources attachées aux réponses
    s, full = req(app, f"/conversations/{conv['id']}", token=token)
    assert [m["role"] for m in full["messages"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert full["messages"][0]["content"] == "combien de jours de télétravail ?"
    assert all(m["content"] for m in full["messages"])
    sources = full["messages"][1]["sources"]
    assert sources and sources[0]["filename"] == "teletravail.txt"

    # la liste montre la conversation et son compte de messages
    _, liste = req(app, "/conversations", token=token)
    assert {"id": conv["id"], "title": "Questions RH", "messages": 4} in liste[
        "conversations"
    ]

    # isolation : un autre utilisateur → 403
    _, autre = req(app, "/signup",
                   {"email": f"gus-{suffixe}@omnes.fr", "name": "Gus"})
    s, body = req(app, f"/conversations/{conv['id']}", token=autre["token"])
    assert (s, body["code"]) == (403, "permission_denied")
