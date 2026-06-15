"""Moteur d'orchestration : `meta` (inventaire de l'app) → `Plan` (réconciliation).

Indépendant du provider et du transport : prend le dict `meta` (produit par
`collect`) + un `Target`, renvoie un `Plan`. Le control plane et le CLI partagent
ce moteur. Un `Provider` optionnel (Scaleway) permet de diffuser le plan contre
l'existant réel (CREATE vs NOOP) ; sans provider, le plan suppose un état vierge
(utile pour le dry-run hors-ligne).
"""

from typing import Optional, List

from .model import Target, Resource, Action, Plan


def _env_suffix(name: str) -> str:
    return name.upper().replace("-", "_")


def desired_resources(meta: dict, target: Target) -> List[Resource]:
    """Les ressources que l'app DÉCLARE (depuis le meta), mappées sur Scaleway.

    Modèle par défaut : mono-container (tous les services dans un Serverless
    Container) + une instance Managed Database partagée avec N bases logiques.
    """
    res: List[Resource] = []

    databases = meta.get("databases") or []
    if databases:
        instance = f"vignemale-{target.app}-{target.env}"
        res.append(Resource(
            "db_instance", instance,
            "Managed Database PostgreSQL (pgvector)",
            f"instance partagée · {len(databases)} base(s) logique(s)",
        ))
        for db in databases:
            res.append(Resource("database", db, "base logique PostgreSQL", f"dans {instance}"))

    for bucket in meta.get("buckets") or []:
        res.append(Resource("bucket", bucket, "Object Storage (bucket S3)"))

    for secret in meta.get("secrets") or []:
        res.append(Resource("secret", secret, "Secret Manager", "valeur fournie au deploy"))

    services = [s.get("name") for s in (meta.get("services") or [])]
    container = f"{target.app}-{target.env}"
    res.append(Resource(
        "container", container, "Serverless Container",
        f"image={target.image or '<à fournir : vignemale build + push>'} · "
        f"services: {', '.join(filter(None, services)) or '—'}",
    ))
    return res


def container_env(meta: dict, target: Target) -> dict:
    """Les VIGNEMALE_* que le deploy posera sur le container (le provider switch).

    Les valeurs réelles (DSN, clés S3, secrets) sont résolues à l'apply ; ici on
    montre QUELLES variables seront injectées — c'est ce qui fait que la même
    image vise le local ou la prod sans changement de code.
    """
    env = {"VIGNEMALE_ADDR": "0.0.0.0:8080"}
    for db in meta.get("databases") or []:
        env[f"VIGNEMALE_SQLDB_{_env_suffix(db)}"] = "<DSN Managed Database>"
    if meta.get("buckets"):
        env["VIGNEMALE_S3_ENDPOINT"] = f"https://s3.{target.region}.scw.cloud"
        env["VIGNEMALE_S3_REGION"] = target.region
        env["VIGNEMALE_S3_ACCESS_KEY"] = "<clé d'accès Object Storage>"
        env["VIGNEMALE_S3_SECRET_KEY"] = "<clé secrète Object Storage>"
    for secret in meta.get("secrets") or []:
        env[f"VIGNEMALE_SECRET_{_env_suffix(secret)}"] = "<injecté depuis Secret Manager>"
    return env


def build_plan(meta: dict, target: Target, provider: Optional["Provider"] = None) -> Plan:
    """Construit le plan de réconciliation. provider=None → plan hors-ligne (tout à créer)."""
    desired = desired_resources(meta, target)
    existing = provider.existing(target) if provider is not None else set()

    actions: List[Action] = []
    for r in desired:
        if (r.kind, r.name) in existing:
            actions.append(Action("noop", r, "déjà présent"))
        else:
            actions.append(Action("create", r, "absent"))

    plan = Plan(target=target, actions=actions, env_vars=container_env(meta, target))

    if not target.image:
        plan.warnings.append(
            "aucune image fournie : `vignemale build` puis pousser au registry "
            "avant le deploy réel (--image <ref>)"
        )
    for secret in meta.get("secrets") or []:
        plan.warnings.append(
            f"secret « {secret} » : la valeur devra être fournie au deploy "
            "(jamais dans le code)"
        )
    return plan
