"""Client typé du service « users » — GÉNÉRÉ par `vignemale gen`, ne pas éditer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vignemale import call
from vignemale._genutil import validate_model

if TYPE_CHECKING:
    from users.groups import NewGroup
    from users.groups import NewMember
    from users.accounts import Signup


def signup(*, body: Signup | dict) -> dict:
    """POST /signup"""
    return call("users", "signup", body=body)


def me() -> dict:
    """GET /me"""
    return call("users", "me")


def create_group(*, body: NewGroup | dict) -> dict:
    """POST /groups"""
    return call("users", "create_group", body=body)


def add_member(*, id: Any, body: NewMember | dict) -> dict:
    """POST /groups/:id/members"""
    return call("users", "add_member", id=id, body=body)


def my_groups() -> dict:
    """GET /my/groups"""
    return call("users", "my_groups")
