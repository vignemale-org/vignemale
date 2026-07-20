"""Order creation — calls `catalog` via the GENERATED client
(`vignemale gen`): typed signature, autocompletion, re-typed return.
Direct locally, signed HTTP once deployed: same code."""

from pydantic import BaseModel

from vignemale import api
from vignemale_clients import catalog


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders")
def create_order(body: Order) -> dict:
    item = catalog.get_item(id=body.item_id)  # → Item (typed), not a dict
    return {"created": True, "item": item, "qty": body.qty}
