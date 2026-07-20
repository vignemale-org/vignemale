"""Example: a shop with stock — the full datamodel, in one file.

- `Table`: typed CRUD, zero SQL (the ORM runs in the Rust core)
- `sql(...)`: custom queries attached to the table (typed, or raw)
- `PII` + `__subject__`: the customer's email is mapped for GDPR
  (`vignemale gdpr map/export/forget examples/boutique.py --subject ada@ex.com`)
- `db.transaction()`: ordering decrements the stock AND creates the order,
  atomically — insufficient stock → exception → automatic ROLLBACK

    vignemale run examples/boutique.py        # the database appears on its own

    curl -X POST 127.0.0.1:8080/products -d '{"name":"ice axe","price":89.9,"stock":3}'
    curl 127.0.0.1:8080/products
    curl -X POST 127.0.0.1:8080/orders \
         -d '{"product_id":1,"email":"ada@example.com","qty":2}'
    curl -X POST 127.0.0.1:8080/orders \
         -d '{"product_id":1,"email":"ada@example.com","qty":99}'   # → 400, stock intact
    curl 127.0.0.1:8080/inventory
"""

from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, SQLDatabase, api, log, serve
from vignemale.datamodel import PII, Table, sql

db = SQLDatabase("boutique")  # for the transaction (raw SQL, a deliberate escape hatch)


class Product(Table):
    __database__ = "boutique"

    id: Optional[int] = None
    name: str
    price: float
    stock: int = 0

    # custom queries attached to the table — typed on return
    in_stock = sql("SELECT * FROM products WHERE stock > 0 ORDER BY name")
    # NAMED and TYPED parameters: $max is declared float, coerced at call time
    affordable = sql(
        "SELECT * FROM products WHERE price <= $max AND stock > 0 ORDER BY price",
        max=float,
    )
    inventory = sql(
        "SELECT count(*) AS products, coalesce(sum(stock), 0) AS pieces, "
        "coalesce(sum(price * stock), 0) AS value FROM products",
        raw=True,
    )


class Order(Table):
    __database__ = "boutique"
    __subject__ = "email"  # GDPR links orders to the customer
    __on_forget__ = "anonymize"

    id: Optional[int] = None
    product_id: int
    email: str = PII(purpose="contact and billing")
    qty: int
    total: float



# the order is created in raw SQL (transaction): make sure the table
# exists first — the ORM CRUD does it on its own, raw SQL does not
Order.ensure_table()


class NewProduct(BaseModel):
    name: str
    price: float
    stock: int = 0


class NewOrder(BaseModel):
    product_id: int
    email: str
    qty: int = 1


@api(method="POST", path="/products")
def create_product(body: NewProduct) -> Product:
    return Product.create(**body.model_dump())


@api(method="GET", path="/products")
def list_products(query) -> dict:
    if "max" in query:  # ?max=100: the query-param string is coerced to float
        return {"products": Product.affordable(max=query["max"])}
    return {"products": Product.in_stock()}


@api(method="GET", path="/inventory")
def inventory() -> dict:
    (stats,) = Product.inventory()
    return stats


@api(method="POST", path="/orders")
def place_order(body: NewOrder) -> dict:
    product = Product.get(body.product_id)
    if product is None:
        raise APIError.not_found(f"unknown product {body.product_id}")

    # atomic: decrement the stock + create the order, or NOTHING —
    # the exception triggers the ROLLBACK, the stock stays intact
    with db.transaction() as tx:
        affected = tx.execute(
            "UPDATE products SET stock = stock - $1 WHERE id = $2 AND stock >= $1",
            body.qty,
            product.id,
        )
        if affected == 0:
            raise APIError.failed_precondition(
                f"insufficient stock for \"{product.name}\" ({product.stock} left)"
            )
        order = tx.query_row(
            "INSERT INTO orders (product_id, email, qty, total) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            product.id,
            body.email,
            body.qty,
            round(product.price * body.qty, 2),
        )

    log.info("order placed", order_id=order["id"], total=order["total"])
    return order


@api(method="GET", path="/orders/:id")
def get_order(id) -> Order:
    order = Order.get(int(id))
    if order is None:
        raise APIError.not_found(f"order {id} not found")
    return order


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
