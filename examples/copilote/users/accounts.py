"""Comptes utilisateurs : inscription, profil, et L'auth handler de l'app.

Zéro SQL : la table est déclarée en Pydantic (`vignemale.model.Table`), créée
automatiquement, et les champs personnels sont tagués `PII` → `vignemale rgpd
map/export/forget` savent quoi cartographier, exporter, effacer.
"""

import secrets
from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, auth_handler, log
from vignemale.datamodel import PII, Table


class User(Table):
    __database__ = "users"
    __subject__ = "id"  # cette table EST la personne

    id: Optional[int] = None
    email: str = PII(purpose="compte et contact")
    name: str = PII(purpose="personnalisation")
    token: str = PII(purpose="authentification")
    plan: str = "free"


class Signup(BaseModel):
    email: str
    name: str


@auth_handler
def check_token(token):
    """Le token (Bearer ou ?token=) est résolu en base → données d'auth."""
    user = User.find_one(token=token)
    if user is None:
        return None  # → 401
    return {"user_id": user.id, "email": user.email, "name": user.name, "plan": user.plan}


@api(method="POST", path="/signup")
def signup(body: Signup) -> dict:
    if "@" not in body.email:
        raise APIError.invalid_argument(f"email invalide : {body.email!r}")
    if User.find_one(email=body.email):
        raise APIError.already_exists(f"un compte existe déjà pour {body.email}")
    user = User.create(
        email=body.email, name=body.name, token="vgm-" + secrets.token_hex(16)
    )
    log.info("compte créé", user_id=user.id, email=user.email)
    return {"user_id": user.id, "name": user.name, "plan": user.plan, "token": user.token}


@api(method="GET", path="/me", auth=True)
def me(auth) -> dict:
    return auth


@api(method="GET", path="/users/:id", auth=True)
def get_user(id) -> dict:
    """Profil d'un utilisateur — consommé par le service `chat` via client."""
    user = User.get(int(id))
    if user is None:
        raise APIError.not_found(f"utilisateur {id} inconnu")
    return {"id": user.id, "name": user.name, "plan": user.plan}
