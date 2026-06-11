"""Service `orders` seul — appelle `catalog` via call() (HTTP signé en test)."""

from pydantic import BaseModel

from vignemale import Service, api, auth_handler, call

orders = Service("orders")


@auth_handler
def check_token(token):
    if token == "sesame":
        return {"user_id": "u-42"}
    return None


class Order(BaseModel):
    item_id: int
    qty: int = 1


@api(method="POST", path="/orders", auth=True)
def create_order(body: Order, auth) -> dict:
    item = call("catalog", "get_item", id=body.item_id)
    return {"created": True, "item": item, "by": auth["user_id"], "qty": body.qty}


if __name__ == "__main__":
    import os

    from vignemale import serve

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
