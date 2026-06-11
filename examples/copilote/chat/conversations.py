"""Conversations : création, liste, lecture — tout est en base, tout est à toi."""

from pydantic import BaseModel

from vignemale import APIError, SQLDatabase, api, log

db = SQLDatabase("chat")

db.execute(
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id         BIGSERIAL PRIMARY KEY,
        user_id    BIGINT NOT NULL,
        title      TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """
)
db.execute(
    """
    CREATE TABLE IF NOT EXISTS messages (
        id              BIGSERIAL PRIMARY KEY,
        conversation_id BIGINT NOT NULL REFERENCES conversations(id),
        role            TEXT NOT NULL,
        content         TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """
)


class NewConversation(BaseModel):
    title: str


def owned_conversation(conversation_id: int, user_id) -> dict:
    """La conversation, si elle appartient bien à l'utilisateur."""
    row = db.query_row("SELECT * FROM conversations WHERE id = $1", conversation_id)
    if row is None:
        raise APIError.not_found(f"conversation {conversation_id} introuvable")
    if row["user_id"] != user_id:
        raise APIError.permission_denied("cette conversation ne t'appartient pas")
    return row


@api(method="POST", path="/conversations", auth=True)
def create_conversation(body: NewConversation, auth) -> dict:
    row = db.query_row(
        "INSERT INTO conversations (user_id, title) VALUES ($1, $2) RETURNING id, title",
        auth["user_id"],
        body.title,
    )
    log.info("conversation créée", conversation_id=row["id"], user_id=auth["user_id"])
    return row


@api(method="GET", path="/conversations", auth=True)
def list_conversations(auth) -> dict:
    rows = db.query(
        """
        SELECT c.id, c.title, count(m.id) AS messages
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        WHERE c.user_id = $1
        GROUP BY c.id, c.title
        ORDER BY c.id
        """,
        auth["user_id"],
    )
    return {"conversations": rows}


@api(method="GET", path="/conversations/:id", auth=True)
def get_conversation(id, auth) -> dict:
    conv = owned_conversation(int(id), auth["user_id"])
    messages = db.query(
        "SELECT role, content FROM messages WHERE conversation_id = $1 ORDER BY id",
        int(id),
    )
    return {"id": conv["id"], "title": conv["title"], "messages": messages}
