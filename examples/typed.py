"""**Typed** example: `@api` with Pydantic models.

Serves two purposes:
  1. runtime validation (Pydantic validates the incoming request),
  2. STATIC extraction (griffe reads the types without running the code → meta graph).
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
