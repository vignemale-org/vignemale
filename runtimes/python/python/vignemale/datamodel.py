"""`vignemale.datamodel` — tables Pydantic : le schéma EST le code, le RGPD aussi.

    from vignemale.datamodel import Table, PII, sql

    class User(Table):
        __database__ = "users"          # la SQLDatabase qui héberge la table
        __subject__ = "id"              # colonne qui identifie LA PERSONNE (RGPD)

        id: int | None = None           # clé primaire auto (BIGSERIAL)
        email: str = PII(purpose="compte")
        name: str = PII(purpose="compte")
        plan: str = "free"

        # requête custom attachée à la table (SQL assumé, typé au retour)
        pros = sql("SELECT * FROM users WHERE plan = $1 ORDER BY id")

    user = User.create(email="ada@ex.com", name="Ada")   # typé, validé
    user = User.find_one(email="ada@ex.com")
    User.pros("pro")                                     # → list[User]

**L'ORM vit dans le core Rust** : ce module décrit la table (un descripteur
JSON) et délègue — génération SQL (sea-query), whitelist des colonnes,
création/migration additive du schéma, exécution. Le SDK n'assemble jamais
de SQL ; un futur SDK (JS…) enverra le même descripteur au même moteur.

RGPD natif : `PII(purpose=…)` marque les données personnelles, `__subject__`
relie chaque ligne à une personne → `vignemale rgpd map/export/forget`.
`__on_forget__` = "delete" (défaut) ou "anonymize".
"""

import datetime
import json
import types as _types
import typing

from pydantic import BaseModel, Field
from pydantic_core import PydanticUndefined

from . import _core
from .sqldb import SQLDatabase, SQLError

# Registre des tables déclarées (pour le RGPD et l'outillage).
_tables: list = []

_UNSET = PydanticUndefined


def PII(default=_UNSET, *, purpose: str = "non précisée"):
    """Marque un champ comme **donnée personnelle** (avec sa finalité)."""
    return Field(default=default, json_schema_extra={"pii": True, "purpose": purpose})


def sql(query: str, raw: bool = False):
    """Requête SQL custom attachée à la table (échappatoire assumée).

        class User(Table):
            ...
            pros = sql("SELECT * FROM users WHERE plan = $1")

        User.pros("pro")        # → list[User] (lignes re-typées dans le modèle)

    `raw=True` → liste de dicts (pour les jointures/agrégats qui ne
    correspondent pas aux colonnes de la table).
    """

    def run(cls, *params):
        cls._ensure()
        rows = cls._db().query(query, *params)
        if raw:
            return rows
        return [cls.model_validate(r) for r in rows]

    return classmethod(run)


# Types logiques envoyés au core (qui fait le mapping SQL).
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
        raise TypeError(f"type de colonne non supporté: {annotation!r}")
    return logical


class Table(BaseModel):
    """Classe de base d'une table. Sous-classe = table (enregistrée au registre)."""

    __database__: typing.ClassVar[str] = ""
    __tablename__: typing.ClassVar[str] = ""
    __subject__: typing.ClassVar[str] = ""  # colonne identifiant la personne
    __on_forget__: typing.ClassVar[str] = "delete"  # delete | anonymize

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.__tablename__:
            cls.__tablename__ = cls.__name__.lower() + "s"
        cls.__ensured = False
        _tables.append(cls)

    # --- plomberie : le descripteur part au core, le core fait le SQL ---

    @classmethod
    def _db(cls) -> SQLDatabase:
        if not cls.__database__:
            raise RuntimeError(f"{cls.__name__}: __database__ non déclaré")
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
        """Crée la table si besoin et ajoute les colonnes manquantes
        (migration additive). Appelée automatiquement par le CRUD ; à appeler
        explicitement avant d'attaquer la table en SQL brut (transactions…)."""
        cls._ensure()

    # --- CRUD (délégué au core) ---

    @classmethod
    def create(cls, **fields):
        obj = cls(**fields)  # validation Pydantic
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
            raise ValueError("save() exige un id — utilise create()")
        values = self.model_dump()
        values.pop("id")
        type(self)._op("update", pk=self.id, values=values)
        return self

    def delete(self) -> None:
        type(self)._op("delete", pk=self.id)

    @classmethod
    def delete_where(cls, **where) -> int:
        if not where:
            raise ValueError("delete_where() exige au moins un critère")
        return cls._op("delete_where", where=where)

    @classmethod
    def update_where(cls, values: dict, **where) -> int:
        """UPDATE en masse (utilisé par l'anonymisation RGPD)."""
        if not where:
            raise ValueError("update_where() exige au moins un critère")
        return cls._op("update_where", values=values, where=where)

    # --- RGPD : introspection ---

    @classmethod
    def pii_fields(cls) -> dict:
        """{champ: finalité} des données personnelles déclarées."""
        out = {}
        for name, f in cls._columns().items():
            extra = f.json_schema_extra or {}
            if isinstance(extra, dict) and extra.get("pii"):
                out[name] = extra.get("purpose", "non précisée")
        return out
