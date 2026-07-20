"""Benchmark app — FastAPI, equivalent endpoints. Launched by uvicorn (port 8081)."""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


@app.get("/hello")
def hello():
    return {"message": "hello world"}


@app.get("/items/{id}")
def item(id: int):
    return {"id": id, "name": "widget"}


class Order(BaseModel):
    item_id: int
    qty: int = 1
    note: str = ""


@app.post("/orders")
def create(body: Order):
    return {"item_id": body.item_id, "qty": body.qty, "total": body.item_id * body.qty}
