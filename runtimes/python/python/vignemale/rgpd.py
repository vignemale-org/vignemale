"""RGPD outillé — parce que le schéma (vignemale.model) connaît les données.

Trois opérations, à la CLI (`vignemale rgpd …`) ou en Python :

- **map** : la carte des données personnelles (table, champ, finalité,
  rattachement à la personne) — l'artefact à donner au juriste ;
- **export** : toutes les données d'UNE personne, en JSON (droit d'accès /
  portabilité, art. 15 & 20) ;
- **forget** : effacement (art. 17) — par table, `__on_forget__` décide :
  `delete` (la ligne disparaît) ou `anonymize` (les champs PII sont
  caviardés, la ligne reste pour les stats).

⚠️ On fournit des **preuves et des mécanismes**, pas une garantie de
conformité juridique — l'humain (DPO/juriste) valide.
"""

from . import datamodel as _model


def data_map() -> list:
    """Inventaire des tables déclarées et de leurs données personnelles."""
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
    """Toutes les données rattachées à une personne, table par table."""
    out = {}
    for t in _subject_tables():
        rows = t.find(**{t.__subject__: subject_id})
        if rows:
            out[t.__tablename__] = [r.model_dump() for r in rows]
    return out


def forget_subject(subject_id, dry_run: bool = False) -> dict:
    """Efface (ou anonymise) les données d'une personne. Renvoie le bilan.

    Les tables sont traitées dans l'ordre inverse de déclaration (les enfants
    d'abord, par convention parents-déclarés-d'abord).
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
    """Caviarde les champs PII (str → '[effacé]', sinon NULL), la ligne reste."""
    pii = t.pii_fields()
    if not pii:
        return
    values = {}
    for name in pii:
        base, _ = _model._unwrap(t.model_fields[name].annotation)
        values[name] = "[effacé]" if base is str else None
    t.update_where(values, **where)
