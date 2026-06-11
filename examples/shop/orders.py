"""Service `orders` — appelle `catalog` comme un client (direct en local,
HTTP signé une fois déployé : même code)."""

from pydantic import BaseModel

from vignemale import Service, api
from vignemale.clients import catalog

orders = Service("orders")


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = catalog.get_item(id=body.item_id)
    return {"created": True, "item": item, "qty": body.qty}
