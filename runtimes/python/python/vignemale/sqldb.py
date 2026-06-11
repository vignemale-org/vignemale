"""Primitive `SQLDatabase` : Postgres depuis Python, servi par le core Rust.

    from vignemale import SQLDatabase

    db = SQLDatabase("todo")
    db.execute("INSERT INTO todos (title) VALUES ($1)", "acheter du pain")
    rows = db.query("SELECT id, title, done FROM todos WHERE done = $1", False)

Provider switch (façon Encore) : le code déclare la base, l'ENVIRONNEMENT
choisit le backend. Le DSN est résolu dans cet ordre :

  1. `VIGNEMALE_SQLDB_<NOM>`  (ex. VIGNEMALE_SQLDB_TODO)
  2. `VIGNEMALE_SQLDB`        (défaut commun à toutes les bases)

En local : un Postgres Docker. En prod : posé par le provisioning (à venir).
Les paramètres sont positionnels, syntaxe Postgres : `$1`, `$2`, …
"""

import json
import os

from . import _core


class SQLError(Exception):
    """Erreur SQL (connexion, requête, type non supporté) — message du core."""


class SQLDatabase:
    def __init__(self, name: str):
        self.name = name

    @property
    def dsn(self) -> str:
        env_key = f"VIGNEMALE_SQLDB_{self.name.upper().replace('-', '_')}"
        dsn = os.environ.get(env_key) or os.environ.get("VIGNEMALE_SQLDB")
        if not dsn:
            raise SQLError(
                f"aucun DSN pour la base '{self.name}' : "
                f"pose {env_key} ou VIGNEMALE_SQLDB "
                "(ex. postgres://user:pass@127.0.0.1:5432/db)"
            )
        return dsn

    def query(self, sql: str, *params) -> list:
        """SELECT → liste de dicts (une entrée par ligne)."""
        try:
            return json.loads(
                _core.sqldb_query(self.dsn, sql, json.dumps(list(params), default=str))
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def query_row(self, sql: str, *params):
        """SELECT → première ligne (dict) ou None."""
        rows = self.query(sql, *params)
        return rows[0] if rows else None

    def execute(self, sql: str, *params) -> int:
        """INSERT/UPDATE/DELETE/DDL → nombre de lignes affectées."""
        try:
            return _core.sqldb_execute(
                self.dsn, sql, json.dumps(list(params), default=str)
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def transaction(self) -> "Transaction":
        """Transaction (context manager) : COMMIT en sortie normale,
        ROLLBACK si une exception traverse le bloc.

            with db.transaction() as tx:
                tx.execute("UPDATE comptes SET solde = solde - $1 WHERE id = $2", 10, a)
                tx.execute("UPDATE comptes SET solde = solde + $1 WHERE id = $2", 10, b)
        """
        return Transaction(self.dsn)


class Transaction:
    """Une transaction Postgres — mêmes méthodes que `SQLDatabase`."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._id = None

    def __enter__(self) -> "Transaction":
        try:
            self._id = _core.sqldb_begin(self._dsn)
        except RuntimeError as e:
            raise SQLError(str(e)) from None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._id is None:
            return
        try:
            if exc_type is None:
                _core.sqldb_tx_commit(self._id)
            else:
                _core.sqldb_tx_rollback(self._id)
        except RuntimeError as e:
            if exc_type is None:  # ne pas masquer l'exception d'origine
                raise SQLError(str(e)) from None
        finally:
            self._id = None

    def query(self, sql: str, *params) -> list:
        try:
            return json.loads(
                _core.sqldb_tx_query(self._id, sql, json.dumps(list(params), default=str))
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def query_row(self, sql: str, *params):
        rows = self.query(sql, *params)
        return rows[0] if rows else None

    def execute(self, sql: str, *params) -> int:
        try:
            return _core.sqldb_tx_execute(
                self._id, sql, json.dumps(list(params), default=str)
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None
