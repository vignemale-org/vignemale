"""Exemple **typé** : `@api` avec modèles Pydantic.

Sert deux buts :
  1. validation au runtime (Pydantic valide la requête entrante),
  2. extraction STATIQUE (griffe lit les types sans exécuter le code → graphe meta).
"""

from pydantic import BaseModel

from vignemale.api import api, serve


class ChatRequest(BaseModel):
    prompt: str
    max_tokens: int = 256


class ChatReply(BaseModel):
    text: str
    tokens: int


@api(method="POST", path="/chat")
def chat(body: ChatRequest) -> ChatReply:
    return ChatReply(text=f"echo: {body.prompt}", tokens=len(body.prompt))


@api(method="GET", path="/health")
def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
