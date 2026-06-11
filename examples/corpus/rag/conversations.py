"""Conversations RAG persistées + agent **Pydantic AI** — un vrai agent.

- L'historique de l'agent (les messages Pydantic AI, sérialisés via
  `ModelMessagesTypeAdapter`) vit en JSONB et est rechargé à chaque tour :
  l'agent a une **mémoire de conversation réelle**, pas un prompt reconstruit.
- Chaque tour fait une recherche vectorielle **filtrée par les permissions**
  (service `kb`, auth propagée) et fournit les extraits autorisés au modèle.
- Modèle : `anthropic:claude-opus-4-8` dès que `ANTHROPIC_API_KEY` est posée
  (`VIGNEMALE_RAG_MODEL` pour en changer) ; `TestModel` sinon (dev/CI
  hors-ligne — c'est le double de test officiel de Pydantic AI).
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
    title: str = PII(purpose="contenu des échanges")
    history: list = []  # mémoire de l'agent (messages Pydantic AI sérialisés)


class ChatMessage(Table):
    __database__ = "corpus_rag"
    __tablename__ = "chat_messages"
    __subject__ = "user_id"
    __on_forget__ = "anonymize"

    id: Optional[int] = None
    conversation_id: int
    user_id: int
    role: str
    content: str = PII(purpose="contenu des échanges")
    sources: list = []


class NewConversation(BaseModel):
    title: str


class Question(BaseModel):
    query: str
    k: int = 4


INSTRUCTIONS = (
    "Tu es l'assistant documentaire de l'entreprise. Réponds UNIQUEMENT à "
    "partir des documents fournis dans le contexte — ils sont déjà filtrés "
    "selon les permissions de l'utilisateur. Cite tes sources entre crochets "
    "[nom-du-fichier]. Si les documents ne permettent pas de répondre, "
    "dis-le clairement plutôt que d'inventer."
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
                "Réponse de test, sources reçues. "
                "(Pose ANTHROPIC_API_KEY pour un vrai modèle Claude.)"
            )
        )
    return Agent(model, instructions=INSTRUCTIONS)


def owned(conversation_id: int, user_id) -> Conversation:
    conv = Conversation.get(conversation_id)
    if conv is None:
        raise APIError.not_found(f"conversation {conversation_id} introuvable")
    if conv.user_id != user_id:
        raise APIError.permission_denied("cette conversation ne t'appartient pas")
    return conv


@api(method="POST", path="/conversations", auth=True)
def create_conversation(body: NewConversation, auth) -> dict:
    conv = Conversation.create(user_id=auth["user_id"], title=body.title)
    log.info("conversation créée", conversation_id=conv.id, user_id=auth["user_id"])
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

    # RAG : recherche vectorielle filtrée par les permissions de CE user
    res = kb.vector_search(body={"embedding": embed(query), "k": body.k if body else 4})
    extraits = res["results"]
    contexte = "\n\n".join(
        f"[source: {e['filename']} · kb: {e['kb']} · score {e['score']}]\n{e['content']}"
        for e in extraits
    ) or "(aucun document accessible pour cet utilisateur)"
    prompt = f"Documents autorisés :\n{contexte}\n\nQuestion : {query}"

    # l'agent répond en streaming, avec sa mémoire rechargée depuis la base
    texte, history = asyncio.run(_run_agent(prompt, conv.history, stream))

    # persistance : les deux messages, les sources, et la mémoire de l'agent
    ChatMessage.create(
        conversation_id=conv.id, user_id=auth["user_id"], role="user", content=query
    )
    ChatMessage.create(
        conversation_id=conv.id,
        user_id=auth["user_id"],
        role="assistant",
        content=texte,
        sources=[
            {"filename": e["filename"], "kb": e["kb"], "score": e["score"]}
            for e in extraits
        ],
    )
    conv.history = history
    conv.save()
    log.info(
        "tour rag",
        conversation_id=conv.id,
        user_id=auth["user_id"],
        sources=len(extraits),
        caracteres=len(texte),
    )


async def _run_agent(prompt: str, raw_history: list, stream):
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    agent = build_agent()
    history = (
        ModelMessagesTypeAdapter.validate_python(raw_history) if raw_history else None
    )
    morceaux = []
    async with agent.run_stream(prompt, message_history=history) as result:
        async for delta in result.stream_text(delta=True):
            morceaux.append(delta)
            stream.write(delta)
    return "".join(morceaux), json.loads(result.all_messages_json())
