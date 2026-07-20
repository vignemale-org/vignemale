"""RAG: the question is embedded here, the search (filtered by the user's
permissions) is delegated to the `kb` service — auth propagates on its own
across the inter-service call.

`/ask` streams an answer built from the authorized excerpts (simulated
LLM: plug your model into `compose_answer`).
"""

import time

from pydantic import BaseModel

from vignemale import api, log
from vignemale_clients import kb

from embedding import embed


class Question(BaseModel):
    query: str
    k: int = 5


@api(method="POST", path="/search", auth=True)
def search(body: Question, auth) -> dict:
    res = kb.vector_search(body={"embedding": embed(body.query), "k": body.k})
    log.info("search", query=body.query, results=len(res["results"]))
    return res


def compose_answer(query: str, excerpts: list) -> str:
    """Your LLM lives here (simulated: it cites the best authorized excerpts)."""
    if not excerpts:
        return (
            f"I found no document you have access to for \"{query}\"."
        )
    sources = ", ".join(sorted({e["filename"] for e in excerpts}))
    best = excerpts[0]["content"].replace("\n", " ")[:300]
    return (
        f"According to your documents ({sources}): {best} "
        f"[score {excerpts[0]['score']}, kb \"{excerpts[0]['kb']}\"]"
    )


@api(method="POST", path="/ask", auth=True, stream=True)
def ask(stream, auth, body: Question = None):
    query = body.query if body else "?"
    res = kb.vector_search(body={"embedding": embed(query), "k": body.k if body else 5})
    answer = compose_answer(query, res["results"])
    for word in answer.split(" "):
        stream.write(word + " ")
        time.sleep(0.02)
    log.info("rag reply", query=query, sources=len(res["results"]))
