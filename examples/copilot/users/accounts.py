"""User accounts: signup, profile, and the app's auth handler.

Zero SQL: the table is declared in Pydantic (`vignemale.model.Table`), created
automatically, and the personal fields are tagged `PII` → `vignemale gdpr
map/export/forget` know what to map, export, erase.
"""

import secrets
from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, auth_handler, log
from vignemale.datamodel import PII, Table


class User(Table):
    __database__ = "users"
    __subject__ = "id"  # this table IS the person

    id: Optional[int] = None
    email: str = PII(purpose="account and contact")
    name: str = PII(purpose="personalization")
    token: str = PII(purpose="authentication")
    plan: str = "free"


class Signup(BaseModel):
    email: str
    name: str


@auth_handler
def check_token(token):
    """The token (Bearer or ?token=) is resolved in the database → auth data."""
    user = User.find_one(token=token)
    if user is None:
        return None  # → 401
    return {"user_id": user.id, "email": user.email, "name": user.name, "plan": user.plan}


@api(method="POST", path="/signup")
def signup(body: Signup) -> dict:
    if "@" not in body.email:
        raise APIError.invalid_argument(f"invalid email: {body.email!r}")
    if User.find_one(email=body.email):
        raise APIError.already_exists(f"an account already exists for {body.email}")
    user = User.create(
        email=body.email, name=body.name, token="vgm-" + secrets.token_hex(16)
    )
    log.info("account created", user_id=user.id, email=user.email)
    return {"user_id": user.id, "name": user.name, "plan": user.plan, "token": user.token}


@api(method="GET", path="/me", auth=True)
def me(auth) -> dict:
    return auth


@api(method="GET", path="/users/:id", auth=True)
def get_user(id) -> dict:
    """A user's profile — consumed by the `chat` service via a client."""
    user = User.get(int(id))
    if user is None:
        raise APIError.not_found(f"unknown user {id}")
    return {"id": user.id, "name": user.name, "plan": user.plan}
