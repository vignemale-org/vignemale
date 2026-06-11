"""RAG : la question est embeddée ici, la recherche (filtrée par les
permissions de l'utilisateur) est déléguée au service `kb` — l'auth se
propage toute seule à travers l'appel inter-services.

`/ask` streame une réponse construite sur les extraits autorisés (LLM
simulé : branche ton modèle dans `compose_answer`).
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
    log.info("recherche", query=body.query, resultats=len(res["results"]))
    return res


def compose_answer(query: str, extraits: list) -> str:
    """Ton LLM vit ici (simulé : il cite les meilleurs extraits autorisés)."""
    if not extraits:
        return (
            f"Je n'ai trouvé aucun document auquel tu as accès pour « {query} »."
        )
    sources = ", ".join(sorted({e["filename"] for e in extraits}))
    meilleur = extraits[0]["content"].replace("\n", " ")[:300]
    return (
        f"D'après tes documents ({sources}) : {meilleur} "
        f"[score {extraits[0]['score']}, kb « {extraits[0]['kb']} »]"
    )


@api(method="POST", path="/ask", auth=True, stream=True)
def ask(stream, auth, body: Question = None):
    query = body.query if body else "?"
    res = kb.vector_search(body={"embedding": embed(query), "k": body.k if body else 5})
    reponse = compose_answer(query, res["results"])
    for mot in reponse.split(" "):
        stream.write(mot + " ")
        time.sleep(0.02)
    log.info("réponse rag", query=query, sources=len(res["results"]))
