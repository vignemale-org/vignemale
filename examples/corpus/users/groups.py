"""Groupes : création, membres — la brique des permissions du RAG."""

from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, log
from vignemale.datamodel import Table

from .accounts import User


class Group(Table):
    __database__ = "corpus_users"

    id: Optional[int] = None
    name: str
    owner_id: int


class GroupMember(Table):
    __database__ = "corpus_users"
    __tablename__ = "group_members"

    id: Optional[int] = None
    group_id: int
    user_id: int


class NewGroup(BaseModel):
    name: str


class NewMember(BaseModel):
    email: str


@api(method="POST", path="/groups", auth=True)
def create_group(body: NewGroup, auth) -> dict:
    group = Group.create(name=body.name, owner_id=auth["user_id"])
    GroupMember.create(group_id=group.id, user_id=auth["user_id"])  # owner membre
    log.info("groupe créé", group_id=group.id, name=group.name)
    return {"id": group.id, "name": group.name}


@api(method="POST", path="/groups/:id/members", auth=True)
def add_member(id, body: NewMember, auth) -> dict:
    group = Group.get(int(id))
    if group is None:
        raise APIError.not_found(f"groupe {id} introuvable")
    if group.owner_id != auth["user_id"]:
        raise APIError.permission_denied("seul le propriétaire ajoute des membres")
    user = User.find_one(email=body.email)
    if user is None:
        raise APIError.not_found(f"aucun compte pour {body.email}")
    if not GroupMember.find_one(group_id=group.id, user_id=user.id):
        GroupMember.create(group_id=group.id, user_id=user.id)
    return {"group_id": group.id, "user_id": user.id}


@api(method="GET", path="/my/groups", auth=True)
def my_groups(auth) -> dict:
    """Les groupes de l'utilisateur — consommé par `kb` (auth propagée)."""
    memberships = GroupMember.find(user_id=auth["user_id"])
    groups = [Group.get(m.group_id) for m in memberships]
    return {"groups": [{"id": g.id, "name": g.name} for g in groups if g]}
