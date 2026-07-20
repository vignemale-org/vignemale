"""Intentionally broken app: a sql() query references a nonexistent column —
`vignemale check --sql` must catch it BEFORE any run."""

from typing import Optional

from vignemale.datamodel import Table, sql


class Broken(Table):
    __database__ = "broken_db"

    id: Optional[int] = None
    name: str

    boom = sql("SELECT nonexistent_column FROM brokens")
