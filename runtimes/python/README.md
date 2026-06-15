# Vignemale

**Déploie tes agents IA et tes APIs en production sur Scaleway, directement depuis ton code Python.**

Vignemale est un framework *Infrastructure-from-Code* : tu **déclares** ton infra
(base de données, bucket, secrets) dans le code, et Vignemale s'occupe du reste —
provisioning, câblage, serveur HTTP. Pas de YAML, pas de Terraform.

```bash
pip install vignemale          # le runtime + le SDK
pip install "vignemale[cli]"   # + l'outil en ligne de commande (run/check/gen/build)
```

## Exemple

Une API todo complète avec Postgres — en un seul fichier, zéro configuration :

```python
from pydantic import BaseModel
from vignemale import api, log, HTTPError, SQLDatabase

# Le code DÉCLARE la base : au `run`, Vignemale provisionne le Postgres local tout
# seul ; en prod, le DSN injecté dans l'environnement prend le relais.
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
    log.info("todo créé", todo_id=row["id"])
    return Todo(**row)

@api(method="GET", path="/todos/{todo_id}")
def get_todo(todo_id: int) -> Todo:
    row = db.query_row("SELECT id, title, done FROM todos WHERE id = $1", todo_id)
    if row is None:
        raise HTTPError(404, "todo introuvable")
    return Todo(**row)
```

```bash
vignemale run app.py     # docker + base + DSN : automatique
curl -X POST 127.0.0.1:8080/todos -d '{"title":"acheter du pain"}'
```

Les endpoints `@api` sont typés avec Pydantic : validation des requêtes/réponses,
erreurs HTTP propres, logs structurés — tout est inclus.

## Ce que le SDK expose

| Brique | Rôle |
|---|---|
| `@api` | endpoint HTTP typé (Pydantic) |
| `SQLDatabase` | Postgres déclaré dans le code (pool + requêtes + transactions) |
| `Bucket` | stockage objet S3-compatible (Scaleway / MinIO / AWS) |
| `Secret` | secrets résolus depuis l'environnement |
| `Service` / `call` | appels service-à-service signés |
| `log` | logs structurés JSON |

Le cœur est écrit en **Rust** (binding PyO3) : serveur HTTP, pools, TLS — la seule
dépendance Python en production est `pydantic`.

## Licence

[MPL-2.0](https://github.com/vignemale-org/vignemale/blob/main/LICENSE) — cœur ouvert.

[Code source & documentation →](https://github.com/vignemale-org/vignemale)
