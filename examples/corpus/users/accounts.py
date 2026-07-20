"""Accounts: signup, token in the database, auth handler."""

import secrets
from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, auth_handler, log
from vignemale.datamodel import PII, Table


class User(Table):
    __database__ = "corpus_users"
    __subject__ = "id"

    id: Optional[int] = None
    email: str = PII(purpose="account and contact")
    name: str = PII(purpose="personalization")
    token: str = PII(purpose="authentication")


class Signup(BaseModel):
    email: str
    name: str


@auth_handler
def check_token(token):
    user = User.find_one(token=token)
    if user is None:
        return None  # → 401
    return {"user_id": user.id, "email": user.email, "name": user.name}


@api(method="POST", path="/signup")
def signup(body: Signup) -> dict:
    if "@" not in body.email:
        raise APIError.invalid_argument(f"invalid email: {body.email!r}")
    if User.find_one(email=body.email):
        raise APIError.already_exists(f"an account already exists for {body.email}")
    user = User.create(
        email=body.email, name=body.name, token="vgm-" + secrets.token_hex(16)
    )
    log.info("account created", user_id=user.id)
    return {"user_id": user.id, "name": user.name, "token": user.token}


@api(method="GET", path="/me", auth=True)
def me(auth) -> dict:
    return auth
