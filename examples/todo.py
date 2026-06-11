"""Exemple « vrai projet » : une API todo avec Postgres, logs structurés et erreurs.

Tout Vignemale en un fichier : `SQLDatabase` (le code déclare la base,
l'environnement fournit le DSN), `@api` typé Pydantic, `HTTPError`, `log`.

    docker run -d --name vignemale-pg -p 5433:5432 -e POSTGRES_PASSWORD=vignemale postgres:16
    export VIGNEMALE_SQLDB_TODO=postgres://postgres:vignemale@127.0.0.1:5433/postgres
    vignemale run examples/todo.py

    curl -X POST 127.0.0.1:8080/todos -d '{"title":"acheter du pain"}'
    curl 127.0.0.1:8080/todos
    curl -X POST 127.0.0.1:8080/todos/1/done
"""

from pydantic import BaseModel

from vignemale import HTTPError, SQLDatabase, api, log, serve

db = SQLDatabase("todo")

db.execute(
    """
    CREATE TABLE IF NOT EXISTS todos (
        id    BIGSERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        done  BOOLEAN NOT NULL DEFAULT FALSE
    )
    """
)


class NewTodo(BaseModel):
    title: str


class Todo(BaseModel):
    id: int
    title: str
    done: bool


@api(method="POST", path="/todos")
def create_todo(body: NewTodo) -> Todo:
    row = db.query_row(
        "INSERT INTO todos (title) VALUES ($1) RETURNING id, title, done",
        body.title,
    )
    log.info("todo créé", todo_id=row["id"], title=row["title"])
    return Todo(**row)


@api(method="GET", path="/todos")
def list_todos() -> dict:
    return {"todos": db.query("SELECT id, title, done FROM todos ORDER BY id")}


@api(method="GET", path="/todos/:id")
def get_todo(id) -> Todo:
    row = db.query_row("SELECT id, title, done FROM todos WHERE id = $1", int(id))
    if row is None:
        raise HTTPError(404, f"todo {id} introuvable")
    return Todo(**row)


@api(method="POST", path="/todos/:id/done")
def complete_todo(id) -> Todo:
    row = db.query_row(
        "UPDATE todos SET done = TRUE WHERE id = $1 RETURNING id, title, done",
        int(id),
    )
    if row is None:
        raise HTTPError(404, f"todo {id} introuvable")
    log.info("todo terminé", todo_id=row["id"])
    return Todo(**row)


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
