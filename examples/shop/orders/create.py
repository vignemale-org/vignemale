"""Création de commandes — appelle `catalog` comme un client (direct en
local, HTTP signé une fois déployé : même code)."""

from pydantic import BaseModel

from vignemale import api
from vignemale.clients import catalog


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = catalog.get_item(id=body.item_id)
    return {"created": True, "item": item, "qty": body.qty}
