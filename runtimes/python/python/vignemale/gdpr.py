"""Tooled GDPR — because the schema (vignemale.model) knows the data.

Three operations, from the CLI (`vignemale gdpr …`) or in Python:

- **map**: the map of personal data (table, field, purpose,
  link to the subject) — the artifact to hand to your legal counsel;
- **export**: all the data of ONE person, in JSON (right of access /
  portability, art. 15 & 20);
- **forget**: erasure (art. 17) — per table, `__on_forget__` decides:
  `delete` (the row disappears) or `anonymize` (the PII fields are
  redacted, the row stays for stats).

Warning: we provide **evidence and mechanisms**, not a guarantee of
legal compliance — a human (DPO/legal counsel) validates.
"""

from . import datamodel as _model


def data_map() -> list:
    """Inventory of the declared tables and their personal data."""
    out = []
    for t in _model._tables:
        fields = []
        for name, f in t._columns().items():
            extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
            fields.append(
                {
                    "name": name,
                    "type": str(f.annotation),
                    "pii": bool(extra.get("pii")),
                    "purpose": extra.get("purpose") if extra.get("pii") else None,
                }
            )
        out.append(
            {
                "table": t.__tablename__,
                "database": t.__database__,
                "model": f"{t.__module__}.{t.__name__}",
                "subject": t.__subject__ or None,
                "on_forget": t.__on_forget__ if t.__subject__ else None,
                "fields": fields,
            }
        )
    return out


def _subject_tables():
    return [t for t in _model._tables if t.__subject__]


def export_subject(subject_id) -> dict:
    """All the data linked to a person, table by table."""
    out = {}
    for t in _subject_tables():
        rows = t.find(**{t.__subject__: subject_id})
        if rows:
            out[t.__tablename__] = [r.model_dump() for r in rows]
    return out


def forget_subject(subject_id, dry_run: bool = False) -> dict:
    """Erases (or anonymizes) a person's data. Returns the summary.

    Tables are processed in reverse declaration order (children
    first, by the parents-declared-first convention).
    """
    report = {}
    for t in reversed(_subject_tables()):
        where = {t.__subject__: subject_id}
        n = t.count(**where)
        if n == 0:
            continue
        action = t.__on_forget__
        if not dry_run:
            if action == "anonymize":
                _anonymize(t, where)
            else:
                t.delete_where(**where)
        report[t.__tablename__] = {"rows": n, "action": action, "dry_run": dry_run}
    return report


def _anonymize(t, where: dict) -> None:
    """Redacts the PII fields (str → '[redacted]', otherwise NULL), the row stays."""
    pii = t.pii_fields()
    if not pii:
        return
    values = {}
    for name in pii:
        base, _ = _model._unwrap(t.model_fields[name].annotation)
        values[name] = "[redacted]" if base is str else None
    t.update_where(values, **where)
