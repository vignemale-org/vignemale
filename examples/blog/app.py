"""Example: Vignemale + a THIRD-PARTY ORM (SQLAlchemy). Like Encore, we don't
force our ORM — we provide the database, the connection, and the migrations;
the ORM of your choice does the rest.

- `SQLDatabase("blog", migrations="migrations")`: database auto-provisioned
  locally; the folder's .sql files are applied on startup (`vignemale run`).
- `db.connection_string`: plug SQLAlchemy (or SQLModel, Tortoise…) onto it.

    vignemale run examples/blog/app.py
    curl -X POST 127.0.0.1:8080/posts -d '{"title":"Hello","body":"first post"}'
    curl 127.0.0.1:8080/posts
"""

from pydantic import BaseModel
from sqlalchemy import create_engine, text

from vignemale import SQLDatabase, api, serve

db = SQLDatabase("blog", migrations="migrations")

# SQLAlchemy connects with ITS driver, via Vignemale's connection string.
_engine = None


def engine():
    global _engine
    if _engine is None:
        url = db.connection_string.replace("postgres://", "postgresql+psycopg://", 1)
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


class NewPost(BaseModel):
    title: str
    body: str


@api(method="POST", path="/posts")
def create_post(body: NewPost) -> dict:
    with engine().begin() as cx:
        row = cx.execute(
            text("INSERT INTO posts (title, body) VALUES (:t, :b) RETURNING id"),
            {"t": body.title, "b": body.body},
        ).one()
    return {"id": row.id, "title": body.title}


@api(method="GET", path="/posts")
def list_posts() -> dict:
    with engine().connect() as cx:
        rows = cx.execute(
            text("SELECT id, title, published FROM posts ORDER BY id")
        ).mappings().all()
    return {"posts": [dict(r) for r in rows]}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
