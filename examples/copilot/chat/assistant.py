"""The assistant: receives a message, stores it, streams its reply token by
token, then archives the reply — all within a persisted conversation.

Along the way, it calls the `users` service like a client (profile/plan):
a direct local call, signed HTTP once deployed.

The "AI" is simulated to stay dependency-free — plug in your favorite agent
framework (Pydantic AI, LangChain…) inside `generate_reply`.
"""

import time

from pydantic import BaseModel

from vignemale import api, log
from vignemale.clients import users

from .conversations import Message, owned_conversation


class UserMessage(BaseModel):
    message: str


def generate_reply(profil: dict, message: str) -> list:
    """Your AI agent lives here. (Simulated: replace with a real model.)"""
    reply = (
        f"Hello {profil['name']}! You tell me: \"{message}\". "
        "Here is my demo-assistant advice: deploy me on "
        "Scaleway with vignemale, and I will answer for real."
    )
    if profil["plan"] == "free":
        reply += " (Upgrade to the pro plan for less generic answers. 😄)"
    return reply.split(" ")


@api(method="POST", path="/conversations/:id/chat", auth=True, stream=True)
def chat_in_conversation(stream, id, auth, body: UserMessage = None):
    conv = owned_conversation(int(id), auth["user_id"])
    message = body.message if body else "…"

    Message.create(
        conversation_id=conv.id, user_id=auth["user_id"], role="user", content=message
    )

    # inter-service call: the profile comes from the `users` service
    profil = users.get_user(id=auth["user_id"])

    tokens = generate_reply(profil, message)
    for word in tokens:
        stream.write(word + " ")
        time.sleep(0.03)

    Message.create(
        conversation_id=conv.id,
        user_id=auth["user_id"],
        role="assistant",
        content=" ".join(tokens),
    )
    log.info(
        "reply generated",
        conversation_id=conv.id,
        user_id=auth["user_id"],
        tokens=len(tokens),
    )
