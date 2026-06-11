"""Service `orders`."""

from pydantic import BaseModel

from vignemale import Service, api

orders = Service("orders")


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    return {"created": True, "item_id": body.item_id, "qty": body.qty}
