"""Service `catalog` — un module = un service (façon Encore)."""

from pydantic import BaseModel

from vignemale import Service, api

catalog = Service("catalog")


class Item(BaseModel):
    id: int
    name: str


@api(method="GET", path="/items/:id")
def get_item(id) -> Item:
    return Item(id=int(id), name="widget")
