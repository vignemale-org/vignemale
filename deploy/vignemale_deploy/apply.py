"""Orchestrateur d'apply : exécute un Plan dans le compte Scaleway du client.

La logique d'orchestration (ordre des étapes, assemblage des DSN, construction de
l'env du container = le provider switch) vit ICI, indépendante du provider concret.
Le `Provider` (interface ci-dessous) ne fait que les appels cloud — Scaleway en
prod, un faux provider en test. C'est ce qui rend l'apply testable sans compte et
réutilisable par le control plane.
"""

from typing import Callable, Dict, Protocol, Set, Tuple

from .model import Target, Deployment
from .engine import _env_suffix


class Provider(Protocol):
    """Ce que l'orchestrateur attend d'un backend cloud (impl. : ScalewayProvider)."""

    def existing(self, target: Target) -> Set[Tuple[str, str]]: ...
    def ensure_databases(self, target: Target, names: list) -> Dict[str, str]: ...  # nom → DSN
    def ensure_bucket(self, target: Target, name: str) -> None: ...
    def deploy_container(
        self,
        target: Target,
        name: str,
        image: str,
        env: Dict[str, str],
        secret_env: Dict[str, str],
    ) -> str: ...  # renvoie l'URL publique du container


def build_runtime_env(
    meta: dict, target: Target, dsns: Dict[str, str], secret_values: Dict[str, str]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Sépare l'env du container en (public, secret) — le provider switch.

    Secret (injecté en variables chiffrées) : DSN, clés S3, secrets applicatifs.
    Public : adresse d'écoute, endpoint/région S3 (non sensibles).
    """
    public: Dict[str, str] = {"VIGNEMALE_ADDR": "0.0.0.0:8080"}
    secret: Dict[str, str] = {}

    for db, dsn in dsns.items():
        secret[f"VIGNEMALE_SQLDB_{_env_suffix(db)}"] = dsn

    if meta.get("buckets"):
        public["VIGNEMALE_S3_ENDPOINT"] = f"https://s3.{target.region}.scw.cloud"
        public["VIGNEMALE_S3_REGION"] = target.region
        # Les clés Object Storage = les clés IAM du client (mêmes creds).
        secret["VIGNEMALE_S3_ACCESS_KEY"] = target.scw_access_key or ""
        secret["VIGNEMALE_S3_SECRET_KEY"] = target.scw_secret_key or ""

    for name in meta.get("secrets") or []:
        val = secret_values.get(name)
        if val is not None:
            secret[f"VIGNEMALE_SECRET_{_env_suffix(name)}"] = val

    return public, secret


def apply_plan(
    meta: dict,
    target: Target,
    provider: Provider,
    secret_values: Dict[str, str] = None,
    on_progress: Callable[[str], None] = None,
) -> Deployment:
    """Réconcilie l'infra puis déploie le container. Idempotent (ensure_*)."""
    secret_values = secret_values or {}
    log = on_progress or (lambda _m: None)
    dep = Deployment(app=target.app, env=target.env)

    if not target.image:
        raise ValueError(
            "apply: --image requis (ref poussée au Container Registry du client)"
        )

    # 1) Bases de données : le provider gère le backend (serverless ou managed)
    #    et renvoie un DSN par base déclarée.
    dsns: Dict[str, str] = {}
    databases = meta.get("databases") or []
    if databases:
        log(f"Bases ({target.db_backend}) : {', '.join(databases)}…")
        dsns = provider.ensure_databases(target, databases)
        dep.steps.extend(f"base {d}" for d in databases)

    # 2) Object Storage (S3).
    for bucket in meta.get("buckets") or []:
        log(f"Object Storage : bucket « {bucket} »…")
        provider.ensure_bucket(target, bucket)
        dep.steps.append(f"bucket {bucket}")

    # 3) Env du container (provider switch) puis déploiement.
    public, secret_env = build_runtime_env(meta, target, dsns, secret_values)
    cont_name = f"{target.app}-{target.env}"
    log(f"Serverless Container : « {cont_name} » (image {target.image})…")
    url = provider.deploy_container(target, cont_name, target.image, public, secret_env)
    dep.url = url
    dep.steps.append(f"container {cont_name} déployé")
    log(f"déployé : {url}")

    missing = [s for s in (meta.get("secrets") or []) if s not in secret_values]
    if missing:
        dep.steps.append(
            "⚠ secrets sans valeur (non injectés) : " + ", ".join(missing)
        )
    return dep
