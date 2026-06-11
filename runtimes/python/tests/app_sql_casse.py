"""App volontairement cassée : une requête sql() référence une colonne
inexistante — `vignemale check --sql` doit la détecter AVANT tout run."""

from typing import Optional

from vignemale.datamodel import Table, sql


class Casse(Table):
    __database__ = "casse_db"

    id: Optional[int] = None
    name: str

    boom = sql("SELECT colonne_inexistante FROM casses")
