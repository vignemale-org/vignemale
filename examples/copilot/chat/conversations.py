"""Conversations: declared models, automatic tables, zero SQL.

The content of the exchanges is personal data (`PII`), linked to the
person via `__subject__`: the right to be forgotten crosses services.
"""

from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, log
from vignemale.datamodel import PII, Table


class Conversation(Table):
    __database__ = "chat"
    __subject__ = "user_id"

    id: Optional[int] = None
    user_id: int
    title: str = PII(purpose="content of the exchanges")


class Message(Table):
    __database__ = "chat"
    __subject__ = "user_id"
    __on_forget__ = "anonymize"  # the row stays (stats), the content is redacted

    id: Optional[int] = None
    conversation_id: int
    user_id: int
    role: str
    content: str = PII(purpose="content of the exchanges")


class NewConversation(BaseModel):
    title: str


def owned_conversation(conversation_id: int, user_id) -> Conversation:
    """The conversation, if it truly belongs to the user."""
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
            {"id": c.id, "title": c.title, "messages": Message.count(conversation_id=c.id)}
            for c in convs
        ]
    }


@api(method="GET", path="/conversations/:id", auth=True)
def get_conversation(id, auth) -> dict:
    conv = owned_conversation(int(id), auth["user_id"])
    messages = Message.find(conversation_id=conv.id)
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }
