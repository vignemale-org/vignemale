"""Exemple : Vignemale + un ORM TIERS (SQLAlchemy). Comme Encore, on ne force
pas notre ORM — on fournit la base, la connexion, et les migrations ; l'ORM
de ton choix fait le reste.

- `SQLDatabase("blog", migrations="migrations")` : base auto-provisionnée en
  local ; les .sql du dossier sont appliqués au démarrage (`vignemale run`).
- `db.connection_string` : branche SQLAlchemy (ou SQLModel, Tortoise…) dessus.

    vignemale run examples/blog/app.py
    curl -X POST 127.0.0.1:8080/posts -d '{"title":"Hello","body":"premier post"}'
    curl 127.0.0.1:8080/posts
"""

from pydantic import BaseModel
from sqlalchemy import create_engine, text

from vignemale import SQLDatabase, api, serve

db = SQLDatabase("blog", migrations="migrations")

# SQLAlchemy se connecte avec SON driver, via la connection string de Vignemale.
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
