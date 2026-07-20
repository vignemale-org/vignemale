# Vignemale

**Deploy your AI agents and APIs to production on Scaleway, straight from your Python code.**

Vignemale is an *Infrastructure-from-Code* framework: you **declare** your infra
(database, bucket, secrets) in the code, and Vignemale handles the rest —
provisioning, wiring, HTTP server. No YAML, no Terraform.

```bash
pip install vignemale          # the runtime + the SDK
pip install "vignemale[cli]"   # + the command-line tool (run/check/gen/build)
```

## Example

A complete todo API with Postgres — in a single file, zero configuration:

```python
from pydantic import BaseModel
from vignemale import api, log, HTTPError, SQLDatabase

# The code DECLARES the database: on `run`, Vignemale provisions the local Postgres
# by itself; in prod, the DSN injected into the environment takes over.
db = SQLDatabase("todo")

db.execute("""
    CREATE TABLE IF NOT EXISTS todos (
        id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, done BOOLEAN NOT NULL DEFAULT FALSE
    )
""")

class NewTodo(BaseModel):
    title: str

class Todo(BaseModel):
    id: int
    title: str
    done: bool

@api(method="POST", path="/todos")
def create_todo(body: NewTodo) -> Todo:
    row = db.query_row(
        "INSERT INTO todos (title) VALUES ($1) RETURNING id, title, done", body.title,
    )
    log.info("todo created", todo_id=row["id"])
    return Todo(**row)

@api(method="GET", path="/todos/{todo_id}")
def get_todo(todo_id: int) -> Todo:
    row = db.query_row("SELECT id, title, done FROM todos WHERE id = $1", todo_id)
    if row is None:
        raise HTTPError(404, "todo not found")
    return Todo(**row)
```

```bash
vignemale run app.py     # docker + database + DSN: automatic
curl -X POST 127.0.0.1:8080/todos -d '{"title":"buy bread"}'
```

The `@api` endpoints are typed with Pydantic: request/response validation, clean
HTTP errors, structured logs — everything is included.

## What the SDK exposes

| Building block | Role |
|---|---|
| `@api` | typed HTTP endpoint (Pydantic) |
| `SQLDatabase` | Postgres declared in the code (pool + queries + transactions) |
| `Bucket` | S3-compatible object storage (Scaleway / MinIO / AWS) |
| `Secret` | secrets resolved from the environment |
| `Service` / `call` | signed service-to-service calls |
| `log` | structured JSON logs |

The core is written in **Rust** (PyO3 binding): HTTP server, pools, TLS — the only
Python dependency in production is `pydantic`.

## License

[MPL-2.0](https://github.com/vignemale-org/vignemale/blob/main/LICENSE) — open core.

[Source code & documentation →](https://github.com/vignemale-org/vignemale)
</content>
