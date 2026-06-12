"""App benchmark — Vignemale. Trois endpoints : JSON simple, param de chemin,
body validé Pydantic. Lancée par bench.sh (port 8080)."""

from pydantic import BaseModel

from vignemale import api, serve


@api(method="GET", path="/hello")
def hello() -> dict:
    return {"message": "hello world"}


@api(method="GET", path="/items/:id")
def item(id) -> dict:
    return {"id": int(id), "name": "widget"}


class Order(BaseModel):
    item_id: int
    qty: int = 1
    note: str = ""


@api(method="POST", path="/orders")
def create(body: Order) -> dict:
    return {"item_id": body.item_id, "qty": body.qty, "total": body.item_id * body.qty}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
