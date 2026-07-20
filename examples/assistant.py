"""All-in-one example: what Vignemale can do today.

  - Pydantic-typed `@api`  → automatic request validation (422 if invalid)
  - path parameter         → `/notes/:id`
  - `HTTPError`            → return a clean 404 from a handler
  - `stream=True`          → token-by-token SSE streaming (the AI-agent case)

Run:  vignemale run examples/assistant.py
  or:  python examples/assistant.py
"""

import time

from pydantic import BaseModel

from vignemale import HTTPError, api, serve


# ---- typed endpoints (validated by Pydantic, statically extracted by `check`) ----

class AskRequest(BaseModel):
    question: str
    lang: str = "en"


class AskReply(BaseModel):
    answer: str
    lang: str


@api(method="POST", path="/ask")
def ask(body: AskRequest) -> AskReply:
    return AskReply(answer=f"You asked: \"{body.question}\"", lang=body.lang)


# ---- path parameter + HTTP error ----

NOTES = {"1": "buy bread", "2": "finish sqldb"}


@api(method="GET", path="/notes/:id")
def get_note(id) -> dict:
    if id not in NOTES:
        raise HTTPError(404, f"note {id} not found")
    return {"id": id, "text": NOTES[id]}


# ---- SSE streaming (the "AI agent" building block) ----

@api(method="POST", path="/chat", stream=True)
def chat(stream, body=None):
    prompt = (body or {}).get("prompt", "hi")
    for mot in f"Simulated reply to \"{prompt}\", streamed word by word.".split(" "):
        stream.write(mot + " ")
        time.sleep(0.1)


@api(method="GET", path="/health")
def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    serve("127.0.0.1:8080")
