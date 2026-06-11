"""Knowledge bases : création, partage à des groupes, et la règle d'accès.

Le contrôle d'accès vit ICI, au plus près des données : une KB est lisible
par son propriétaire et par les membres des groupes auxquels elle est
partagée. `accessible_kb_ids()` interroge `users` (auth propagée) pour les
groupes, puis résout en SQL — c'est ce filtre que la recherche vectorielle
applique.
"""

from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, SQLDatabase, api, log
from vignemale.datamodel import Table
from vignemale_clients import users

db = SQLDatabase("corpus_kb")


class KnowledgeBase(Table):
    __database__ = "corpus_kb"
    __tablename__ = "kbs"

    id: Optional[int] = None
    name: str
    owner_id: int


class KbAccess(Table):
    __database__ = "corpus_kb"
    __tablename__ = "kb_access"

    id: Optional[int] = None
    kb_id: int
    group_id: int


class NewKb(BaseModel):
    name: str


class Grant(BaseModel):
    group_id: int


def accessible_kb_ids(auth) -> list:
    """KB du propriétaire + KB partagées aux groupes de l'utilisateur."""
    ids = {k.id for k in KnowledgeBase.find(owner_id=auth["user_id"])}
    groups = users.my_groups()  # appel inter-services, auth propagée
    for g in groups["groups"]:
        ids.update(a.kb_id for a in KbAccess.find(group_id=g["id"]))
    return sorted(ids)


def owned_kb(kb_id: int, auth) -> KnowledgeBase:
    base = KnowledgeBase.get(kb_id)
    if base is None:
        raise APIError.not_found(f"knowledge base {kb_id} introuvable")
    if base.owner_id != auth["user_id"]:
        raise APIError.permission_denied("réservé au propriétaire de la KB")
    return base


@api(method="POST", path="/kbs", auth=True)
def create_kb(body: NewKb, auth) -> dict:
    base = KnowledgeBase.create(name=body.name, owner_id=auth["user_id"])
    log.info("kb créée", kb_id=base.id, name=base.name)
    return {"id": base.id, "name": base.name}


@api(method="POST", path="/kbs/:id/grant", auth=True)
def grant_kb(id, body: Grant, auth) -> dict:
    base = owned_kb(int(id), auth)
    if not KbAccess.find_one(kb_id=base.id, group_id=body.group_id):
        KbAccess.create(kb_id=base.id, group_id=body.group_id)
    log.info("kb partagée", kb_id=base.id, group_id=body.group_id)
    return {"kb_id": base.id, "group_id": body.group_id}


@api(method="GET", path="/kbs", auth=True)
def list_kbs(auth) -> dict:
    ids = accessible_kb_ids(auth)
    bases = [KnowledgeBase.get(i) for i in ids]
    return {"kbs": [{"id": b.id, "name": b.name} for b in bases if b]}
