"""Service `orders` — appelle `catalog` via call() (direct en local, HTTP
signé une fois déployé : même code)."""

from pydantic import BaseModel

from vignemale import Service, api, call

orders = Service("orders")


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = call("catalog", "get_item", id=body.item_id)
    return {"created": True, "item": item, "qty": body.qty}
