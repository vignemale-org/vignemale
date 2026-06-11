"""Comptes utilisateurs : inscription, profil, et L'auth handler de l'app
(branché sur la base — le token est vérifié en SQL à chaque requête)."""

import secrets

from pydantic import BaseModel

from vignemale import APIError, SQLDatabase, api, auth_handler, log

db = SQLDatabase("users")

db.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        id         BIGSERIAL PRIMARY KEY,
        email      TEXT NOT NULL UNIQUE,
        name       TEXT NOT NULL,
        token      TEXT NOT NULL UNIQUE,
        plan       TEXT NOT NULL DEFAULT 'free',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """
)


class Signup(BaseModel):
    email: str
    name: str


@auth_handler
def check_token(token):
    """Le token (Bearer ou ?token=) est résolu en base → données d'auth."""
    row = db.query_row(
        "SELECT id, email, name, plan FROM users WHERE token = $1", token
    )
    if row is None:
        return None  # → 401
    return {"user_id": row["id"], "email": row["email"], "name": row["name"], "plan": row["plan"]}


@api(method="POST", path="/signup")
def signup(body: Signup) -> dict:
    if "@" not in body.email:
        raise APIError.invalid_argument(f"email invalide : {body.email!r}")
    if db.query_row("SELECT 1 FROM users WHERE email = $1", body.email):
        raise APIError.already_exists(f"un compte existe déjà pour {body.email}")
    token = "vgm-" + secrets.token_hex(16)
    row = db.query_row(
        "INSERT INTO users (email, name, token) VALUES ($1, $2, $3) RETURNING id, plan",
        body.email,
        body.name,
        token,
    )
    log.info("compte créé", user_id=row["id"], email=body.email)
    return {"user_id": row["id"], "name": body.name, "plan": row["plan"], "token": token}


@api(method="GET", path="/me", auth=True)
def me(auth) -> dict:
    return auth


@api(method="GET", path="/users/:id", auth=True)
def get_user(id) -> dict:
    """Profil d'un utilisateur — consommé par le service `chat` via client."""
    row = db.query_row("SELECT id, name, plan FROM users WHERE id = $1", int(id))
    if row is None:
        raise APIError.not_found(f"utilisateur {id} inconnu")
    return row
