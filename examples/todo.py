"""A "real project" example: a todo API with Postgres, structured logs and errors.

All of Vignemale in one file: `SQLDatabase` (the code DECLARES the database — on
`run`, Vignemale provisions the local Postgres on its own), Pydantic-typed
`@api`, `HTTPError`, `log`. Zero configuration:

    vignemale run examples/todo.py      # docker + database + DSN: automatic

    curl -X POST 127.0.0.1:8080/todos -d '{"title":"buy bread"}'
    curl 127.0.0.1:8080/todos
    curl -X POST 127.0.0.1:8080/todos/1/done

(In production, the same code will point to a managed database: the DSN set in
the environment — VIGNEMALE_SQLDB_TODO — takes priority over the local one.)
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
    log.info("todo created", todo_id=row["id"], title=row["title"])
    return Todo(**row)


@api(method="GET", path="/todos")
def list_todos() -> dict:
    return {"todos": db.query("SELECT id, title, done FROM todos ORDER BY id")}


@api(method="GET", path="/todos/:id")
def get_todo(id) -> Todo:
    row = db.query_row("SELECT id, title, done FROM todos WHERE id = $1", int(id))
    if row is None:
        raise HTTPError(404, f"todo {id} not found")
    return Todo(**row)


@api(method="POST", path="/todos/:id/done")
def complete_todo(id) -> Todo:
    row = db.query_row(
        "UPDATE todos SET done = TRUE WHERE id = $1 RETURNING id, title, done",
        int(id),
    )
    if row is None:
        raise HTTPError(404, f"todo {id} not found")
    log.info("todo completed", todo_id=row["id"])
    return Todo(**row)


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
