"""Exemple : une boutique avec stock — le datamodel au complet, en un fichier.

- `Table` : CRUD typé, zéro SQL (l'ORM tourne dans le core Rust)
- `sql(...)` : requêtes custom attachées à la table (typées, ou brutes)
- `PII` + `__subject__` : l'email du client est cartographié pour le RGPD
  (`vignemale rgpd map/export/forget examples/boutique.py --subject ada@ex.com`)
- `db.transaction()` : commander décrémente le stock ET crée la commande,
  atomiquement — stock insuffisant → exception → ROLLBACK automatique

    vignemale run examples/boutique.py        # la base apparaît toute seule

    curl -X POST 127.0.0.1:8080/produits -d '{"name":"piolet","prix":89.9,"stock":3}'
    curl 127.0.0.1:8080/produits
    curl -X POST 127.0.0.1:8080/commandes \
         -d '{"produit_id":1,"email":"ada@example.com","qty":2}'
    curl -X POST 127.0.0.1:8080/commandes \
         -d '{"produit_id":1,"email":"ada@example.com","qty":99}'   # → 400, stock intact
    curl 127.0.0.1:8080/inventaire
"""

from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, SQLDatabase, api, log, serve
from vignemale.datamodel import PII, Table, sql

db = SQLDatabase("boutique")  # pour la transaction (le SQL brut, échappatoire assumée)


class Produit(Table):
    __database__ = "boutique"

    id: Optional[int] = None
    name: str
    prix: float
    stock: int = 0

    # requêtes custom attachées à la table — typées au retour
    en_stock = sql("SELECT * FROM produits WHERE stock > 0 ORDER BY name")
    # paramètres NOMMÉS et TYPÉS : $max est déclaré float, coercé à l'appel
    abordables = sql(
        "SELECT * FROM produits WHERE prix <= $max AND stock > 0 ORDER BY prix",
        max=float,
    )
    inventaire = sql(
        "SELECT count(*) AS produits, coalesce(sum(stock), 0) AS pieces, "
        "coalesce(sum(prix * stock), 0) AS valeur FROM produits",
        raw=True,
    )


class Commande(Table):
    __database__ = "boutique"
    __subject__ = "email"  # le RGPD rattache les commandes au client
    __on_forget__ = "anonymize"

    id: Optional[int] = None
    produit_id: int
    email: str = PII(purpose="contact et facturation")
    qty: int
    total: float



# la commande est créée en SQL brut (transaction) : on s'assure que la table
# existe avant — le CRUD ORM le fait tout seul, le SQL brut non
Commande.ensure_table()


class NouveauProduit(BaseModel):
    name: str
    prix: float
    stock: int = 0


class NouvelleCommande(BaseModel):
    produit_id: int
    email: str
    qty: int = 1


@api(method="POST", path="/produits")
def create_produit(body: NouveauProduit) -> Produit:
    return Produit.create(**body.model_dump())


@api(method="GET", path="/produits")
def list_produits(query) -> dict:
    if "max" in query:  # ?max=100 : la string du query param est coercée en float
        return {"produits": Produit.abordables(max=query["max"])}
    return {"produits": Produit.en_stock()}


@api(method="GET", path="/inventaire")
def inventaire() -> dict:
    (stats,) = Produit.inventaire()
    return stats


@api(method="POST", path="/commandes")
def commander(body: NouvelleCommande) -> dict:
    produit = Produit.get(body.produit_id)
    if produit is None:
        raise APIError.not_found(f"produit {body.produit_id} inconnu")

    # atomique : décrément du stock + création de la commande, ou RIEN —
    # l'exception déclenche le ROLLBACK, le stock reste intact
    with db.transaction() as tx:
        touche = tx.execute(
            "UPDATE produits SET stock = stock - $1 WHERE id = $2 AND stock >= $1",
            body.qty,
            produit.id,
        )
        if touche == 0:
            raise APIError.failed_precondition(
                f"stock insuffisant pour « {produit.name} » ({produit.stock} restant)"
            )
        commande = tx.query_row(
            "INSERT INTO commandes (produit_id, email, qty, total) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            produit.id,
            body.email,
            body.qty,
            round(produit.prix * body.qty, 2),
        )

    log.info("commande passée", commande_id=commande["id"], total=commande["total"])
    return commande


@api(method="GET", path="/commandes/:id")
def get_commande(id) -> Commande:
    commande = Commande.get(int(id))
    if commande is None:
        raise APIError.not_found(f"commande {id} introuvable")
    return commande


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
