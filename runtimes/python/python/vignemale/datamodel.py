"""`vignemale.datamodel` — tables Pydantic : le schéma EST le code, le RGPD aussi.

    from vignemale.datamodel import Table, PII

    class User(Table):
        __database__ = "users"          # la SQLDatabase qui héberge la table
        __subject__ = "id"              # colonne qui identifie LA PERSONNE (RGPD)

        id: int | None = None           # clé primaire auto (BIGSERIAL)
        email: str = PII(purpose="compte")
        name: str = PII(purpose="compte")
        plan: str = "free"

    user = User.create(email="ada@ex.com", name="Ada")   # typé, validé
    user = User.find_one(email="ada@ex.com")
    user.plan = "pro"; user.save()

Ce que ça apporte :
- **CRUD typé** (create/get/find/find_one/count/save/delete) — zéro SQL à la
  main ; le SQL brut (`SQLDatabase.query`) reste l'échappatoire assumée pour
  les requêtes complexes (jointures, agrégats…).
- **Schéma automatique** : la table est créée au premier usage, et les
  colonnes ajoutées au modèle sont ajoutées à la table (migration additive).
- **RGPD natif** : `PII(purpose=…)` marque les données personnelles,
  `__subject__` relie chaque ligne à une personne → `vignemale rgpd
  map/export/forget` (cf. vignemale.rgpd). `__on_forget__` = "delete"
  (défaut) ou "anonymize" (les champs PII sont caviardés, la ligne reste).

Tout passe par le core Rust (pool, logs, provisioning local automatique).
"""

import datetime
import types as _types
import typing

from pydantic import BaseModel, Field
from pydantic_core import PydanticUndefined

from .sqldb import SQLDatabase

# Registre des tables déclarées (pour le RGPD et l'outillage).
_tables: list = []

_UNSET = PydanticUndefined


def PII(default=_UNSET, *, purpose: str = "non précisée"):
    """Marque un champ comme **donnée personnelle** (avec sa finalité)."""
    return Field(default=default, json_schema_extra={"pii": True, "purpose": purpose})


_SQL_TYPES = {
    int: "BIGINT",
    str: "TEXT",
    bool: "BOOLEAN",
    float: "DOUBLE PRECISION",
    datetime.datetime: "TIMESTAMPTZ",
    datetime.date: "DATE",
    dict: "JSONB",
    list: "JSONB",
}


_UNION_TYPES = (typing.Union, getattr(_types, "UnionType", typing.Union))


def _unwrap(annotation):
    """`Optional[T]` / `T | None` → (T, nullable)."""
    if typing.get_origin(annotation) in _UNION_TYPES:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _sql_type(annotation) -> str:
    base, _ = _unwrap(annotation)
    base = typing.get_origin(base) or base  # list[str] → list, dict[...] → dict
    sql = _SQL_TYPES.get(base)
    if sql is None:
        raise TypeError(f"type de colonne non supporté: {annotation!r}")
    return sql


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

    # --- plomberie ---

    @classmethod
    def _db(cls) -> SQLDatabase:
        if not cls.__database__:
            raise RuntimeError(f"{cls.__name__}: __database__ non déclaré")
        return SQLDatabase(cls.__database__)

    @classmethod
    def _columns(cls) -> dict:
        return {n: f for n, f in cls.model_fields.items()}

    @classmethod
    def _ensure(cls) -> None:
        """Crée la table au premier usage ; ajoute les colonnes manquantes
        (migration additive — les changements destructifs restent manuels)."""
        if cls.__ensured:
            return
        db, table = cls._db(), cls.__tablename__
        cols = []
        for name, f in cls._columns().items():
            if name == "id":
                cols.append('"id" BIGSERIAL PRIMARY KEY')
                continue
            _, nullable = _unwrap(f.annotation)
            not_null = "" if (nullable or not f.is_required()) else " NOT NULL"
            cols.append(f'"{name}" {_sql_type(f.annotation)}{not_null}')
        db.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(cols)})')

        existing = {
            r["column_name"]
            for r in db.query(
                "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
                table,
            )
        }
        for name, f in cls._columns().items():
            if name not in existing:
                db.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{name}" {_sql_type(f.annotation)}'
                )
        cls.__ensured = True

    @classmethod
    def _where(cls, where: dict, start: int = 1):
        if not where:
            return "", []
        parts, values, i = [], [], start
        for k, v in where.items():
            if v is None:
                parts.append(f'"{k}" IS NULL')
            else:
                parts.append(f'"{k}" = ${i}')
                values.append(v)
                i += 1
        return " WHERE " + " AND ".join(parts), values

    # --- CRUD ---

    @classmethod
    def create(cls, **fields):
        cls._ensure()
        obj = cls(**fields)  # validation Pydantic
        data = obj.model_dump()
        if data.get("id") is None:
            data.pop("id", None)
        names = ", ".join(f'"{c}"' for c in data)
        ph = ", ".join(f"${i + 1}" for i in range(len(data)))
        row = cls._db().query_row(
            f'INSERT INTO "{cls.__tablename__}" ({names}) VALUES ({ph}) RETURNING *',
            *data.values(),
        )
        return cls.model_validate(row)

    @classmethod
    def get(cls, id):
        cls._ensure()
        row = cls._db().query_row(
            f'SELECT * FROM "{cls.__tablename__}" WHERE "id" = $1', id
        )
        return cls.model_validate(row) if row is not None else None

    @classmethod
    def find(cls, **where) -> list:
        cls._ensure()
        cond, values = cls._where(where)
        order = ' ORDER BY "id"' if "id" in cls.model_fields else ""
        rows = cls._db().query(
            f'SELECT * FROM "{cls.__tablename__}"{cond}{order}', *values
        )
        return [cls.model_validate(r) for r in rows]

    @classmethod
    def find_one(cls, **where):
        rows = cls.find(**where)
        return rows[0] if rows else None

    @classmethod
    def count(cls, **where) -> int:
        cls._ensure()
        cond, values = cls._where(where)
        row = cls._db().query_row(
            f'SELECT count(*) AS n FROM "{cls.__tablename__}"{cond}', *values
        )
        return row["n"]

    def save(self):
        type(self)._ensure()
        if getattr(self, "id", None) is None:
            raise ValueError("save() exige un id — utilise create()")
        data = self.model_dump()
        data.pop("id")
        sets = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(data))
        type(self)._db().execute(
            f'UPDATE "{self.__tablename__}" SET {sets} WHERE "id" = ${len(data) + 1}',
            *data.values(),
            self.id,
        )
        return self

    def delete(self) -> None:
        type(self)._ensure()
        type(self)._db().execute(
            f'DELETE FROM "{self.__tablename__}" WHERE "id" = $1', self.id
        )

    @classmethod
    def delete_where(cls, **where) -> int:
        cls._ensure()
        cond, values = cls._where(where)
        if not cond:
            raise ValueError("delete_where() exige au moins un critère")
        return cls._db().execute(
            f'DELETE FROM "{cls.__tablename__}"{cond}', *values
        )

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
