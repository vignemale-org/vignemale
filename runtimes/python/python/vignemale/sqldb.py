"""`SQLDatabase` primitive: Postgres from Python, served by the Rust core.

    from vignemale import SQLDatabase

    db = SQLDatabase("todo")
    db.execute("INSERT INTO todos (title) VALUES ($1)", "buy bread")
    rows = db.query("SELECT id, title, done FROM todos WHERE done = $1", False)

Provider switch (Encore-style): the code declares the database, the ENVIRONMENT
chooses the backend. The DSN is resolved in this order:

  1. `VIGNEMALE_SQLDB_<NAME>`  (e.g. VIGNEMALE_SQLDB_TODO)
  2. `VIGNEMALE_SQLDB`         (shared default for all databases)

Locally: a Docker Postgres. In prod: set by provisioning.

Two ways to use it (like Encore):
- direct queries (`query`/`execute`/`transaction`), served by the core;
- **any ORM** (SQLAlchemy, SQLModel, Tortoise…) via
  `db.connection_string` — the ORM connects with its own driver. We
  provide the database + the connection + the migrations; the ORM does the rest.
"""

import glob
import json
import os

from . import _core

# Databases declared with a migrations directory (applied by `vignemale run`).
_databases: list = []


class SQLError(Exception):
    """SQL error (connection, query, unsupported type) — message from the core."""


class SQLDatabase:
    def __init__(self, name: str, migrations: str = None):
        """`migrations`: directory of `.sql` files (sorted by name) each applied
        once at startup (`vignemale run`), Encore-style — for schemas
        managed by a third-party ORM/tool (alembic, etc.)."""
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
                f"no DSN for database '{self.name}': "
                f"set {env_key} or VIGNEMALE_SQLDB "
                "(e.g. postgres://user:pass@127.0.0.1:5432/db)"
            )
        return dsn

    @property
    def connection_string(self) -> str:
        """The DSN, to plug in the ORM of your choice (SQLAlchemy, SQLModel…).

            engine = create_engine(db.connection_string.replace(
                "postgres://", "postgresql+psycopg://", 1))
        """
        return self.dsn

    def migrate(self) -> int:
        """Applies the migration files not yet applied (idempotent,
        tracked in `_vignemale_migrations`). Returns the number applied."""
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
            # migration + recording in ONE atomic batch: if the
            # migration fails, nothing is marked as applied (rollback).
            self.batch(
                "BEGIN;\n" + sql.rstrip().rstrip(";") + ";\n"
                f"INSERT INTO _vignemale_migrations (name) VALUES ('{safe}');\nCOMMIT;"
            )
            applied += 1
        return applied

    def batch(self, sql: str) -> None:
        """Executes a multi-statement SQL script (without parameters)."""
        try:
            _core.sqldb_batch(self.dsn, sql)
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def query(self, sql: str, *params) -> list:
        """SELECT → list of dicts (one entry per row)."""
        try:
            return json.loads(
                _core.sqldb_query(self.dsn, sql, json.dumps(list(params), default=str))
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def query_row(self, sql: str, *params):
        """SELECT → first row (dict) or None."""
        rows = self.query(sql, *params)
        return rows[0] if rows else None

    def execute(self, sql: str, *params) -> int:
        """INSERT/UPDATE/DELETE/DDL → number of affected rows."""
        try:
            return _core.sqldb_execute(
                self.dsn, sql, json.dumps(list(params), default=str)
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None

    def transaction(self) -> "Transaction":
        """Transaction (context manager): COMMIT on normal exit,
        ROLLBACK if an exception crosses the block.

            with db.transaction() as tx:
                tx.execute("UPDATE accounts SET balance = balance - $1 WHERE id = $2", 10, a)
                tx.execute("UPDATE accounts SET balance = balance + $1 WHERE id = $2", 10, b)
        """
        return Transaction(self.dsn)


class Transaction:
    """A Postgres transaction — same methods as `SQLDatabase`."""

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
            if exc_type is None:  # do not mask the original exception
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
