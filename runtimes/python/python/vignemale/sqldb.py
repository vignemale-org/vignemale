"""Primitive `SQLDatabase` : Postgres depuis Python, servi par le core Rust.

    from vignemale import SQLDatabase

    db = SQLDatabase("todo")
    db.execute("INSERT INTO todos (title) VALUES ($1)", "acheter du pain")
    rows = db.query("SELECT id, title, done FROM todos WHERE done = $1", False)

Provider switch (façon Encore) : le code déclare la base, l'ENVIRONNEMENT
choisit le backend. Le DSN est résolu dans cet ordre :

  1. `VIGNEMALE_SQLDB_<NOM>`  (ex. VIGNEMALE_SQLDB_TODO)
  2. `VIGNEMALE_SQLDB`        (défaut commun à toutes les bases)

En local : un Postgres Docker. En prod : posé par le provisioning.

Deux façons de l'utiliser (comme Encore) :
- requêtes directes (`query`/`execute`/`transaction`), servies par le core ;
- **n'importe quel ORM** (SQLAlchemy, SQLModel, Tortoise…) via
  `db.connection_string` — l'ORM se connecte avec son propre driver. On
  fournit la base + la connexion + les migrations ; l'ORM fait le reste.
"""

import glob
import json
import os

from . import _core

# Bases déclarées avec un dossier de migrations (appliquées par `vignemale run`).
_databases: list = []


class SQLError(Exception):
    """Erreur SQL (connexion, requête, type non supporté) — message du core."""


class SQLDatabase:
    def __init__(self, name: str, migrations: str = None):
        """`migrations` : dossier de fichiers `.sql` (triés par nom) appliqués
        une fois chacun au démarrage (`vignemale run`), façon Encore — pour les
        schémas gérés par un ORM/outil tiers (alembic, etc.)."""
        self.name = name
        self._migrations = None
        if migrations:
            import inspect

            base = os.path.dirname(
                os.path.abspath(inspect.stack()[1].frame.f_globals.get("__file__", "."))
            )
            self._migrations = (
                migrations if os.path.isabs(migrations)
                else os.path.normpath(os.path.join(base, migrations))
            )
        _databases.append(self)

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

    @property
    def connection_string(self) -> str:
        """Le DSN, pour brancher l'ORM de ton choix (SQLAlchemy, SQLModel…).

            engine = create_engine(db.connection_string.replace(
                "postgres://", "postgresql+psycopg://", 1))
        """
        return self.dsn

    def migrate(self) -> int:
        """Applique les fichiers de migration non encore appliqués (idempotent,
        suivis dans `_vignemale_migrations`). Renvoie le nombre appliqué."""
        if not self._migrations or not os.path.isdir(self._migrations):
            return 0
        self.batch(
            "CREATE TABLE IF NOT EXISTS _vignemale_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {
            r["name"]
            for r in self.query("SELECT name FROM _vignemale_migrations")
        }
        files = sorted(
            f for f in glob.glob(os.path.join(self._migrations, "*.sql"))
        )
        applied = 0
        for path in files:
            name = os.path.basename(path)
            if name in done:
                continue
            with open(path) as f:
                sql = f.read()
            safe = name.replace("'", "''")
            # migration + enregistrement dans UN batch atomique : si la
            # migration échoue, rien n'est marqué appliqué (rollback).
            self.batch(
                "BEGIN;\n" + sql.rstrip().rstrip(";") + ";\n"
                f"INSERT INTO _vignemale_migrations (name) VALUES ('{safe}');\nCOMMIT;"
            )
            applied += 1
        return applied

    def batch(self, sql: str) -> None:
        """Exécute un script SQL multi-instructions (sans paramètres)."""
        try:
            _core.sqldb_batch(self.dsn, sql)
        except RuntimeError as e:
            raise SQLError(str(e)) from None

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
