"""Exemple « tout-en-un » : ce que Vignemale sait faire aujourd'hui.

  - `@api` typé Pydantic  → validation auto de la requête (422 si invalide)
  - paramètre de chemin   → `/notes/:id`
  - `HTTPError`           → renvoyer un 404 propre depuis un handler
  - `stream=True`         → streaming SSE token par token (le cas agent IA)

Lancer :  vignemale run examples/assistant.py
   ou  :  python examples/assistant.py
"""

import time

from pydantic import BaseModel

from vignemale import HTTPError, api, serve


# ---- endpoints typés (validés par Pydantic, extraits en statique par `check`) ----

class AskRequest(BaseModel):
    question: str
    lang: str = "fr"


class AskReply(BaseModel):
    answer: str
    lang: str


@api(method="POST", path="/ask")
def ask(body: AskRequest) -> AskReply:
    return AskReply(answer=f"Tu as demandé : « {body.question} »", lang=body.lang)


# ---- paramètre de chemin + erreur HTTP ----

NOTES = {"1": "acheter du pain", "2": "finir sqldb"}


@api(method="GET", path="/notes/:id")
def get_note(id) -> dict:
    if id not in NOTES:
        raise HTTPError(404, f"note {id} introuvable")
    return {"id": id, "text": NOTES[id]}


# ---- streaming SSE (la brique « agent IA ») ----

@api(method="POST", path="/chat", stream=True)
def chat(stream, body=None):
    prompt = (body or {}).get("prompt", "salut")
    for mot in f"Réponse simulée à « {prompt} », streamée mot à mot.".split(" "):
        stream.write(mot + " ")
        time.sleep(0.1)


@api(method="GET", path="/health")
def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    serve("127.0.0.1:8080")
