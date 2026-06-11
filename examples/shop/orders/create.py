"""Création de commandes — appelle `catalog` via le client GÉNÉRÉ
(`vignemale gen`) : signature typée, autocomplétion, retour re-typé.
Direct en local, HTTP signé une fois déployé : même code."""

from pydantic import BaseModel

from vignemale import api
from vignemale_clients import catalog


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = catalog.get_item(id=body.item_id)  # → Item (typé), pas un dict
    return {"created": True, "item": item, "qty": body.qty}
