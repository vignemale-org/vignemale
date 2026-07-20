"""The catalog service's "items" endpoints."""

from pydantic import BaseModel

from vignemale import api


class Item(BaseModel):
    id: int
    name: str


@api(method="GET", path="/items/:id")
def get_item(id) -> Item:
    return Item(id=int(id), name="widget")
