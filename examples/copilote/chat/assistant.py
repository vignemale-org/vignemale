"""L'assistant : reçoit un message, le stocke, streame sa réponse token par
token, puis archive la réponse — le tout dans une conversation persistée.

Au passage, il appelle le service `users` comme un client (profil/plan) :
appel direct en local, HTTP signé une fois déployé.

L'« IA » est simulée pour rester sans dépendance — branche ton framework
d'agent préféré (Pydantic AI, LangChain…) dans `generate_reply`.
"""

import time

from pydantic import BaseModel

from vignemale import api, log
from vignemale.clients import users

from .conversations import Message, owned_conversation


class UserMessage(BaseModel):
    message: str


def generate_reply(profil: dict, message: str) -> list:
    """Ton agent IA vit ici. (Simulé : remplace par un vrai modèle.)"""
    reply = (
        f"Bonjour {profil['name']} ! Tu me dis : « {message} ». "
        "Voici mon conseil d'assistant de démonstration : déploie-moi sur "
        "Scaleway avec vignemale, et je répondrai pour de vrai."
    )
    if profil["plan"] == "free":
        reply += " (Passe au plan pro pour des réponses moins génériques. 😄)"
    return reply.split(" ")


@api(method="POST", path="/conversations/:id/chat", auth=True, stream=True)
def chat_in_conversation(stream, id, auth, body: UserMessage = None):
    conv = owned_conversation(int(id), auth["user_id"])
    message = body.message if body else "…"

    Message.create(
        conversation_id=conv.id, user_id=auth["user_id"], role="user", content=message
    )

    # appel inter-services : le profil vient du service `users`
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
        "réponse générée",
        conversation_id=conv.id,
        user_id=auth["user_id"],
        tokens=len(tokens),
    )
