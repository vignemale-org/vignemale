"""Persisted RAG conversations + **Pydantic AI** agent — a real agent.

- The agent's history (the Pydantic AI messages, serialized via
  `ModelMessagesTypeAdapter`) lives in JSONB and is reloaded on every turn:
  the agent has a **real conversation memory**, not a rebuilt prompt.
- Each turn runs a vector search **filtered by permissions**
  (`kb` service, auth propagated) and feeds the authorized excerpts to the model.
- Model: `anthropic:claude-opus-4-8` as soon as `ANTHROPIC_API_KEY` is set
  (`VIGNEMALE_RAG_MODEL` to change it); `TestModel` otherwise (offline
  dev/CI — it's Pydantic AI's official test double).
"""

import asyncio
import json
import os
from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, log
from vignemale.datamodel import PII, Table
from vignemale_clients import kb

from embedding import embed


class Conversation(Table):
    __database__ = "corpus_rag"
    __subject__ = "user_id"

    id: Optional[int] = None
    user_id: int
    title: str = PII(purpose="content of the exchanges")
    history: list = []  # agent memory (serialized Pydantic AI messages)


class ChatMessage(Table):
    __database__ = "corpus_rag"
    __tablename__ = "chat_messages"
    __subject__ = "user_id"
    __on_forget__ = "anonymize"

    id: Optional[int] = None
    conversation_id: int
    user_id: int
    role: str
    content: str = PII(purpose="content of the exchanges")
    sources: list = []


class NewConversation(BaseModel):
    title: str


class Question(BaseModel):
    query: str
    k: int = 4


INSTRUCTIONS = (
    "You are the company's document assistant. Answer ONLY from the "
    "documents provided in the context — they are already filtered "
    "according to the user's permissions. Cite your sources in brackets "
    "[filename]. If the documents do not allow you to answer, "
    "say so clearly rather than making things up."
)


def build_agent():
    from pydantic_ai import Agent

    model = os.environ.get("VIGNEMALE_RAG_MODEL")
    if not model:
        model = (
            "anthropic:claude-opus-4-8"
            if os.environ.get("ANTHROPIC_API_KEY")
            else "test"
        )
    if model == "test":
        from pydantic_ai.models.test import TestModel

        model = TestModel(
            custom_output_text=(
                "Test reply, sources received. "
                "(Set ANTHROPIC_API_KEY for a real Claude model.)"
            )
        )
    return Agent(model, instructions=INSTRUCTIONS)


def owned(conversation_id: int, user_id) -> Conversation:
    conv = Conversation.get(conversation_id)
    if conv is None:
        raise APIError.not_found(f"conversation {conversation_id} not found")
    if conv.user_id != user_id:
        raise APIError.permission_denied("this conversation does not belong to you")
    return conv


@api(method="POST", path="/conversations", auth=True)
def create_conversation(body: NewConversation, auth) -> dict:
    conv = Conversation.create(user_id=auth["user_id"], title=body.title)
    log.info("conversation created", conversation_id=conv.id, user_id=auth["user_id"])
    return {"id": conv.id, "title": conv.title}


@api(method="GET", path="/conversations", auth=True)
def list_conversations(auth) -> dict:
    convs = Conversation.find(user_id=auth["user_id"])
    return {
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "messages": ChatMessage.count(conversation_id=c.id),
            }
            for c in convs
        ]
    }


@api(method="GET", path="/conversations/:id", auth=True)
def get_conversation(id, auth) -> dict:
    conv = owned(int(id), auth["user_id"])
    messages = ChatMessage.find(conversation_id=conv.id)
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "sources": m.sources}
            for m in messages
        ],
    }


@api(method="POST", path="/conversations/:id/ask", auth=True, stream=True)
def ask_in_conversation(stream, id, auth, body: Question = None):
    conv = owned(int(id), auth["user_id"])
    query = body.query if body else "?"

    # RAG: vector search filtered by THIS user's permissions
    res = kb.vector_search(body={"embedding": embed(query), "k": body.k if body else 4})
    excerpts = res["results"]
    context = "\n\n".join(
        f"[source: {e['filename']} · kb: {e['kb']} · score {e['score']}]\n{e['content']}"
        for e in excerpts
    ) or "(no document accessible for this user)"
    prompt = f"Authorized documents:\n{context}\n\nQuestion: {query}"

    # the agent replies in streaming, with its memory reloaded from the database
    text, history = asyncio.run(_run_agent(prompt, conv.history, stream))

    # persistence: both messages, the sources, and the agent's memory
    ChatMessage.create(
        conversation_id=conv.id, user_id=auth["user_id"], role="user", content=query
    )
    ChatMessage.create(
        conversation_id=conv.id,
        user_id=auth["user_id"],
        role="assistant",
        content=text,
        sources=[
            {"filename": e["filename"], "kb": e["kb"], "score": e["score"]}
            for e in excerpts
        ],
    )
    conv.history = history
    conv.save()
    log.info(
        "rag turn",
        conversation_id=conv.id,
        user_id=auth["user_id"],
        sources=len(excerpts),
        characters=len(text),
    )


async def _run_agent(prompt: str, raw_history: list, stream):
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    agent = build_agent()
    history = (
        ModelMessagesTypeAdapter.validate_python(raw_history) if raw_history else None
    )
    chunks = []
    async with agent.run_stream(prompt, message_history=history) as result:
        async for delta in result.stream_text(delta=True):
            chunks.append(delta)
            stream.write(delta)
    return "".join(chunks), json.loads(result.all_messages_json())
