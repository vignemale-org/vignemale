"""`vignemale init [nom]` : scaffold un projet Vignemale prêt à `run`.

Pose une archi de base minimale — un fichier `app.py` avec un `@api` typé (qui
tourne sans aucune infra externe), un `pyproject.toml` avec `[tool.vignemale]`,
un `.gitignore` et un README. Objectif : `pip install vignemale` → `vignemale
init mon-app` → `vignemale run` en moins d'une minute.

Sans nom (ou `.`) : initialise le dossier courant. Ne réécrit jamais un fichier
existant (app.py / pyproject.toml) — on s'arrête plutôt que d'écraser.
"""

import os
import re
import sys


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")[:48]


APP_PY = '''\
"""{app} — une app Vignemale.

Infrastructure-from-Code : tu écris du Python, Vignemale déduit et provisionne
l'infra (local au `run`, cloud au `deploy`). Lance :

    vignemale run app.py
    curl 127.0.0.1:8080/hello
    curl -X POST 127.0.0.1:8080/greet -d '{{"name":"Ada"}}'
"""

from pydantic import BaseModel

from vignemale import api, log


@api(method="GET", path="/hello")
def hello() -> dict:
    return {{"msg": "salut depuis {app} 👋"}}


class Who(BaseModel):
    name: str


@api(method="POST", path="/greet")
def greet(body: Who) -> dict:
    log.info("greet", name=body.name)
    return {{"msg": f"bonjour, {{body.name}} !"}}


# ── Étape suivante : une base de données, sans config ──────────────────────
# Décommente ce bloc — au `run`, Vignemale provisionne un Postgres local tout
# seul (docker) et pose le DSN. En prod, le même code vise une base managée.
#
# from vignemale import SQLDatabase
#
# db = SQLDatabase("{slug}")
# db.execute("CREATE TABLE IF NOT EXISTS notes (id BIGSERIAL PRIMARY KEY, body TEXT)")
#
# @api(method="GET", path="/notes")
# def notes() -> dict:
#     return {{"notes": db.query("SELECT id, body FROM notes ORDER BY id")}}
'''

PYPROJECT = '''\
[project]
name = "{slug}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["vignemale>=0.1.3"]

[tool.vignemale]
# Nom de l'app côté Vignemale Cloud (remote git : /<app>.git).
app = "{slug}"
# mono  : un seul container   ·   services : un container par service + gateway
topology = "mono"
'''

GITIGNORE = '''\
__pycache__/
*.py[cod]
.venv/
.env
# clients de services générés par `vignemale gen`
vignemale_clients/
'''

README = '''\
# {app}

App [Vignemale](https://github.com/vignemale-org/vignemale) — Infrastructure-from-Code en Python.

## Démarrer

```bash
pip install vignemale
vignemale run app.py
```

```bash
curl 127.0.0.1:8080/hello
curl -X POST 127.0.0.1:8080/greet -d '{{"name":"Ada"}}'
```

## Déployer

```bash
vignemale login              # une fois
vignemale link {slug}         # rattache ce dépôt au projet (créé dans le panel)
vignemale deploy             # push-to-deploy
```
'''


def init(name: str = ".", path: str = ".") -> int:
    # Cible : `.` → dossier courant ; sinon un nouveau sous-dossier <slug>.
    if name in (".", ""):
        target = os.path.abspath(path)
        slug = _slug(os.path.basename(target))
    else:
        slug = _slug(name)
        if not slug:
            print("Nom de projet invalide (lettres, chiffres, tirets).", file=sys.stderr)
            return 1
        target = os.path.abspath(os.path.join(path, slug))
        os.makedirs(target, exist_ok=True)

    if not slug:
        print("Impossible de déduire un nom d'app depuis le dossier courant ; "
              "donne un nom : vignemale init mon-app", file=sys.stderr)
        return 1

    files = {
        "app.py": APP_PY.format(app=slug, slug=slug),
        "pyproject.toml": PYPROJECT.format(slug=slug),
        ".gitignore": GITIGNORE,
        "README.md": README.format(app=slug, slug=slug),
    }

    # On n'écrase jamais app.py / pyproject.toml : sécurité contre un init dans
    # un projet déjà existant.
    for guard in ("app.py", "pyproject.toml"):
        if os.path.exists(os.path.join(target, guard)):
            print(f"{guard} existe déjà dans {target} — abandon (rien écrit).",
                  file=sys.stderr)
            return 1

    for rel, content in files.items():
        with open(os.path.join(target, rel), "w", encoding="utf-8") as f:
            f.write(content)

    where = "." if target == os.path.abspath(path) and name in (".", "") else slug
    print(f"vignemale: ✓ projet « {slug} » créé.")
    if where != ".":
        print(f"vignemale:   cd {where}")
    print("vignemale:   vignemale run app.py")
    return 0
