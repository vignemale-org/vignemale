"""`vignemale.datamodel` — Pydantic tables: the schema IS the code, GDPR too.

    from vignemale.datamodel import Table, PII, sql

    class User(Table):
        __database__ = "users"          # the SQLDatabase hosting the table
        __subject__ = "id"              # column identifying THE PERSON (GDPR)

        id: int | None = None           # auto primary key (BIGSERIAL)
        email: str = PII(purpose="account")
        name: str = PII(purpose="account")
        plan: str = "free"

        # custom query attached to the table (SQL by design, typed return)
        pros = sql("SELECT * FROM users WHERE plan = $1 ORDER BY id")

    user = User.create(email="ada@ex.com", name="Ada")   # typed, validated
    user = User.find_one(email="ada@ex.com")
    User.pros("pro")                                     # → list[User]

**The ORM lives in the Rust core**: this module describes the table (a JSON
descriptor) and delegates — SQL generation (sea-query), column whitelist,
additive schema creation/migration, execution. The SDK never assembles SQL;
a future SDK (JS…) will send the same descriptor to the same engine.

Native GDPR: `PII(purpose=…)` marks personal data, `__subject__` links each
row to a person → `vignemale gdpr map/export/forget`.
`__on_forget__` = "delete" (default) or "anonymize".
"""

import datetime
import json
import types as _types
import typing

from pydantic import BaseModel, Field
from pydantic_core import PydanticUndefined

from . import _core
from .sqldb import SQLDatabase, SQLError

# Registry of declared tables (for GDPR and the tooling).
_tables: list = []

# Registry of declared sql() queries: (class, attribute, compiled query)
# — for the PREPARE validation of `vignemale check --sql`.
_sql_queries: list = []

_UNSET = PydanticUndefined


def PII(default=_UNSET, *, purpose: str = "unspecified"):
    """Marks a field as **personal data** (with its purpose)."""
    return Field(default=default, json_schema_extra={"pii": True, "purpose": purpose})


def sql(query: str, raw: bool = False, **param_types):
    """Custom SQL query attached to the table (an escape hatch, by design).

    **Named and typed parameters** (validated/coerced by Pydantic at call time):

        class User(Table):
            ...
            pros = sql(
                "SELECT * FROM users WHERE plan = $plan AND age >= $age",
                plan=str, age=int,
            )

        User.pros(plan="pro", age=18)     # → list[User]
        User.pros("pro", "18")            # positional ok; "18" coerced to int

    Without declared types, positional placeholders `$1, $2…` remain
    supported: `sql("… WHERE plan = $1")` then `User.pros("pro")`.

    `raw=True` → list of dicts (joins/aggregates that do not map to the
    table's columns).
    """
    if not param_types:

        def run(cls, *params):
            cls._ensure()
            rows = cls._db().query(query, *params)
            return rows if raw else [cls.model_validate(r) for r in rows]

        run.__vignemale_sql__ = query  # for the PREPARE validation (check --sql)
        return classmethod(run)

    import re

    from pydantic import TypeAdapter

    order = list(param_types)
    adapters = {name: TypeAdapter(t) for name, t in param_types.items()}

    used = set(re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", query))
    unknown_in_query = used - set(order)
    if unknown_in_query:
        raise TypeError(
            f"sql(): parameter(s) not declared in the query: "
            f"{', '.join(sorted(unknown_in_query))}"
        )
    unused = set(order) - used
    if unused:
        raise TypeError(
            f"sql(): parameter(s) declared but absent from the query: "
            f"{', '.join(sorted(unused))}"
        )
    # $name → $N (multiple occurrences share the same placeholder)
    compiled = re.sub(
        r"\$([a-zA-Z_][a-zA-Z0-9_]*)",
        lambda m: f"${order.index(m.group(1)) + 1}",
        query,
    )

    def run(cls, *args, **kwargs):
        cls._ensure()
        values = dict(zip(order, args))
        overlap = set(values) & set(kwargs)
        if overlap:
            raise TypeError(f"duplicate parameter(s): {', '.join(sorted(overlap))}")
        values.update(kwargs)
        extra = set(values) - set(order)
        if extra:
            raise TypeError(f"unknown parameter(s): {', '.join(sorted(extra))}")
        missing = [n for n in order if n not in values]
        if missing:
            raise TypeError(f"missing parameter(s): {', '.join(missing)}")
        bound = [adapters[n].validate_python(values[n]) for n in order]
        rows = cls._db().query(compiled, *bound)
        return rows if raw else [cls.model_validate(r) for r in rows]

    run.__vignemale_sql__ = compiled  # for the PREPARE validation (check --sql)
    return classmethod(run)


# Logical types sent to the core (which does the SQL mapping).
_LOGICAL_TYPES = {
    int: "int",
    str: "str",
    bool: "bool",
    float: "float",
    datetime.datetime: "datetime",
    datetime.date: "date",
    dict: "json",
    list: "json",
}

_UNION_TYPES = (typing.Union, getattr(_types, "UnionType", typing.Union))


def _unwrap(annotation):
    """`Optional[T]` / `T | None` → (T, nullable)."""
    if typing.get_origin(annotation) in _UNION_TYPES:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _logical_type(annotation) -> str:
    base, _ = _unwrap(annotation)
    base = typing.get_origin(base) or base  # list[str] → list, dict[...] → dict
    logical = _LOGICAL_TYPES.get(base)
    if logical is None:
        raise TypeError(f"unsupported column type: {annotation!r}")
    return logical


class Table(BaseModel):
    """Base class for a table. Subclass = table (added to the registry)."""

    __database__: typing.ClassVar[str] = ""
    __tablename__: typing.ClassVar[str] = ""
    __subject__: typing.ClassVar[str] = ""  # column identifying the person
    __on_forget__: typing.ClassVar[str] = "delete"  # delete | anonymize

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.__tablename__:
            cls.__tablename__ = cls.__name__.lower() + "s"
        cls.__ensured = False
        _tables.append(cls)
        for attr, value in cls.__dict__.items():
            fn = getattr(value, "__func__", None)
            compiled = getattr(fn, "__vignemale_sql__", None)
            if compiled:
                _sql_queries.append((cls, attr, compiled))

    # --- plumbing: the descriptor goes to the core, the core does the SQL ---

    @classmethod
    def _db(cls) -> SQLDatabase:
        if not cls.__database__:
            raise RuntimeError(f"{cls.__name__}: __database__ not declared")
        return SQLDatabase(cls.__database__)

    @classmethod
    def _columns(cls) -> dict:
        return {n: f for n, f in cls.model_fields.items()}

    @classmethod
    def _schema(cls) -> str:
        columns = []
        for name, f in cls._columns().items():
            _, nullable = _unwrap(f.annotation)
            columns.append(
                {
                    "name": name,
                    "typ": _logical_type(f.annotation),
                    "nullable": nullable or not f.is_required(),
                    "primary_key": name == "id",
                }
            )
        return json.dumps({"table": cls.__tablename__, "columns": columns})

    @classmethod
    def _raw_op(cls, op: str, **args):
        try:
            out = _core.sqldb_orm(
                cls._db().dsn, op, cls._schema(), json.dumps(args, default=str)
            )
        except RuntimeError as e:
            raise SQLError(str(e)) from None
        return json.loads(out)

    @classmethod
    def _op(cls, op: str, **args):
        cls._ensure()
        return cls._raw_op(op, **args)

    @classmethod
    def _ensure(cls) -> None:
        if cls.__ensured:
            return
        cls._raw_op("ensure")
        cls.__ensured = True

    @classmethod
    def ensure_table(cls) -> None:
        """Creates the table if needed and adds the missing columns
        (additive migration). Called automatically by the CRUD; call it
        explicitly before hitting the table in raw SQL (transactions…)."""
        cls._ensure()

    # --- CRUD (delegated to the core) ---

    @classmethod
    def create(cls, **fields):
        obj = cls(**fields)  # Pydantic validation
        values = obj.model_dump()
        if values.get("id") is None:
            values.pop("id", None)
        return cls.model_validate(cls._op("insert", values=values))

    @classmethod
    def get(cls, id):
        row = cls._op("get", pk=id)
        return cls.model_validate(row) if row is not None else None

    @classmethod
    def find(cls, **where) -> list:
        return [cls.model_validate(r) for r in cls._op("find", where=where)]

    @classmethod
    def find_one(cls, **where):
        rows = cls.find(**where)
        return rows[0] if rows else None

    @classmethod
    def count(cls, **where) -> int:
        return cls._op("count", where=where)

    def save(self):
        if getattr(self, "id", None) is None:
            raise ValueError("save() requires an id — use create()")
        values = self.model_dump()
        values.pop("id")
        type(self)._op("update", pk=self.id, values=values)
        return self

    def delete(self) -> None:
        type(self)._op("delete", pk=self.id)

    @classmethod
    def delete_where(cls, **where) -> int:
        if not where:
            raise ValueError("delete_where() requires at least one criterion")
        return cls._op("delete_where", where=where)

    @classmethod
    def update_where(cls, values: dict, **where) -> int:
        """Bulk UPDATE (used by the GDPR anonymization)."""
        if not where:
            raise ValueError("update_where() requires at least one criterion")
        return cls._op("update_where", values=values, where=where)

    # --- GDPR: introspection ---

    @classmethod
    def _prepare_check(cls, query: str) -> dict:
        try:
            out = _core.sqldb_prepare(cls._db().dsn, query)
        except RuntimeError as e:
            raise SQLError(str(e)) from None
        return json.loads(out)

    @classmethod
    def pii_fields(cls) -> dict:
        """{field: purpose} of the declared personal data."""
        out = {}
        for name, f in cls._columns().items():
            extra = f.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("pii"):
                out[name] = extra.get("purpose", "unspecified")
        return out


def check_sql_queries() -> list:
    """Validates each declared `sql()` query with a Postgres PREPARE — the
    mechanism of `sqlx::query!`, at `vignemale check` time: syntax, tables,
    columns, inferred types. Nothing is executed.

    Returns one report per query: {query, ok, params?, columns?, error?}.
    """
    for t in _tables:
        t.ensure_table()  # the tables must exist for PREPARE to validate
    report = []
    for cls, attr, compiled in _sql_queries:
        label = f"{cls.__name__}.{attr}"
        try:
            info = cls._prepare_check(compiled)
            report.append({"query": label, "ok": True, **info})
        except SQLError as e:
            report.append({"query": label, "ok": False, "error": str(e)})
    return report
