"""Provider Scaleway : exécute le plan via le SDK officiel `scaleway`.

Colle fine au-dessus du SDK (cf. docs/phase4-deploy.md §8) :
  serverless_sqldb.v1alpha1 → Serverless SQL Database (DÉFAUT, scale-to-zero)
  rdb.v1                     → Managed Database (option --db managed)
  container.v1beta1          → namespace + container + deploy
  Object Storage             → S3 pur via boto3

Bases :
  - serverless : `create_database(cpu_min=0,…)`, auth IAM (user = id du principal
    de la clé API, password = clé secrète) → DSN reconstructible, AUCUN état local.
  - managed    : instance partagée + bases logiques ; le mot de passe n'étant pas
    récupérable, on le persiste dans `~/.vignemale/deploy-state/`.

Idempotence : par nom (`vignemale-<app>-<env>-<db>` pour le serverless) ou par
tags (`vignemale-app/env` pour instances RDB et containers).
"""

import json
import os
import secrets as _secrets
import string
from pathlib import Path
from typing import Dict, Set, Tuple

from .model import Target, DbEndpoint

# --- Managed Database (option) ---
_DB_ENGINE = os.environ.get("VIGNEMALE_SCW_DB_ENGINE", "PostgreSQL-15")  # VERIFY
_DB_NODE_TYPE = os.environ.get("VIGNEMALE_SCW_DB_NODE", "DB-DEV-S")       # VERIFY
_DB_VOLUME_GB = int(os.environ.get("VIGNEMALE_SCW_DB_VOLUME_GB", "10"))
_DB_USER = "vignemale"
_STATE_DIR = Path(os.environ.get(
    "VIGNEMALE_STATE_DIR", str(Path.home() / ".vignemale" / "deploy-state")
))
# --- Serverless SQL Database (défaut) ---
_SDB_CPU_MAX = int(os.environ.get("VIGNEMALE_SCW_SDB_CPU_MAX", "4"))


def _tags(target: Target) -> list:
    return [f"vignemale-app={target.app}", f"vignemale-env={target.env}"]


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())


def _sdb_name(target: Target, db: str) -> str:
    return _sanitize(f"vignemale-{target.app}-{target.env}-{db}")


def _gen_password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(_secrets.choice(alphabet) for _ in range(n))


class ScalewayProvider:
    def __init__(self, target: Target):
        if not (target.scw_access_key and target.scw_secret_key and target.scw_project_id):
            raise ValueError(
                "ScalewayProvider exige scw_access_key / scw_secret_key / "
                "scw_project_id sur le Target"
            )
        self.target = target
        try:
            from scaleway import Client
            from scaleway.container.v1beta1 import ContainerV1Beta1API
        except ImportError as e:  # pragma: no cover
            raise SystemExit(
                "vignemale deploy: SDK Scaleway requis — "
                "`pip install 'vignemale-deploy[scaleway]'`"
            ) from e

        self._client = Client(
            access_key=target.scw_access_key,
            secret_key=target.scw_secret_key,
            default_project_id=target.scw_project_id,
            default_region=target.region,
        )
        self._container = ContainerV1Beta1API(self._client)
        self._principal = None  # résolu paresseusement (serverless)

    # ---- bases de données (dispatch backend) ----

    def ensure_databases(self, target: Target, names: list) -> Dict[str, str]:
        if target.db_backend == "managed":
            return self._ensure_managed(target, names)
        return self._ensure_serverless(target, names)

    # ---- Serverless SQL Database (défaut) ----

    def _principal_id(self) -> str:
        """ID du user/application IAM porteur de la clé API (= user du DSN)."""
        if self._principal is None:
            from scaleway.iam.v1alpha1 import IamV1Alpha1API
            key = IamV1Alpha1API(self._client).get_api_key(
                access_key=self.target.scw_access_key
            )
            self._principal = key.user_id or key.application_id
        return self._principal

    def _ensure_serverless(self, target: Target, names: list) -> Dict[str, str]:
        from scaleway.serverless_sqldb.v1alpha1 import ServerlessSqldbV1Alpha1API
        api = ServerlessSqldbV1Alpha1API(self._client)
        principal = self._principal_id()
        secret = target.scw_secret_key
        existing = {d.name: d for d in api.list_databases_all()}

        dsns: Dict[str, str] = {}
        for declared in names:
            full = _sdb_name(target, declared)
            db = existing.get(full)
            if db is None:
                db = api.create_database(
                    name=full, cpu_min=0, cpu_max=_SDB_CPU_MAX,  # cpu_min=0 → scale-to-zero
                    project_id=target.scw_project_id,
                )
                db = api.wait_for_database(database_id=db.id)
            host = db.endpoint
            hostport = host if ":" in host else f"{host}:5432"
            dsns[declared] = (
                f"postgresql://{principal}:{secret}@{hostport}/{full}?sslmode=require"
            )
        return dsns

    # ---- Managed Database (option --db managed) ----

    def _ensure_managed(self, target: Target, names: list) -> Dict[str, str]:
        from scaleway.rdb.v1 import RdbV1API
        rdb = RdbV1API(self._client)
        inst_name = f"vignemale-{target.app}-{target.env}"
        endpoint = self._ensure_rdb_instance(rdb, target, inst_name)
        dsns: Dict[str, str] = {}
        for declared in names:
            dbname = declared.lower().replace("-", "_")
            if not any(d.name == dbname for d in rdb.list_databases_all(instance_id=endpoint.instance_id)):
                rdb.create_database(instance_id=endpoint.instance_id, name=dbname)
            dsns[declared] = endpoint.dsn(dbname)
        return dsns

    def _ensure_rdb_instance(self, rdb, target: Target, name: str) -> DbEndpoint:
        state = self._load_state()
        if state.get("db", {}).get("name") == name and state["db"].get("password"):
            d = state["db"]
            return DbEndpoint(d["instance_id"], d["host"], d["port"], d["user"], d["password"])
        if any(i.name == name for i in rdb.list_instances_all()):
            raise SystemExit(
                f"vignemale deploy: instance « {name} » existe côté Scaleway mais son "
                "mot de passe n'est pas dans l'état local (~/.vignemale/deploy-state). "
                "Le control plane stockera cet état dans sa base."
            )
        password = _gen_password()
        inst = rdb.create_instance(
            engine=_DB_ENGINE, user_name=_DB_USER, password=password,
            node_type=_DB_NODE_TYPE, is_ha_cluster=False, disable_backup=False,
            volume_size=_DB_VOLUME_GB * 1000 * 1000 * 1000, backup_same_region=True,
            name=name, project_id=target.scw_project_id, tags=_tags(target),
        )
        inst = rdb.wait_for_instance(instance_id=inst.id)
        ep = (inst.endpoints or [None])[0]
        if ep is None:
            raise SystemExit(f"vignemale deploy: instance « {name} » sans endpoint.")
        host = getattr(ep, "ip", None) or getattr(getattr(ep, "load_balancer", None), "name", "")
        port = getattr(ep, "port", 5432)
        self._save_state({"db": {
            "name": name, "instance_id": inst.id, "host": str(host),
            "port": int(port), "user": _DB_USER, "password": password,
        }})
        return DbEndpoint(inst.id, str(host), int(port), _DB_USER, password)

    def _state_path(self) -> Path:
        return _STATE_DIR / f"{self.target.app}-{self.target.env}.json"

    def _load_state(self) -> dict:
        p = self._state_path()
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_state(self, state: dict) -> None:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state, indent=2))

    # ---- lookup (lecture seule) ----

    def existing(self, target: Target) -> Set[Tuple[str, str]]:
        found: Set[Tuple[str, str]] = set()
        wanted = set(_tags(target))
        prefix = _sanitize(f"vignemale-{target.app}-{target.env}-")
        # serverless databases (par préfixe de nom — pas de tags sur ce produit)
        try:
            from scaleway.serverless_sqldb.v1alpha1 import ServerlessSqldbV1Alpha1API
            for d in ServerlessSqldbV1Alpha1API(self._client).list_databases_all():
                if d.name.startswith(prefix):
                    found.add(("database", d.name[len(prefix):]))
        except Exception:
            pass
        # containers (par tags)
        for ns in self._container.list_namespaces_all():
            for c in self._container.list_containers_all(namespace_id=ns.id):
                if wanted.issubset(set(c.tags or [])):
                    found.add(("container", c.name))
        # buckets
        try:
            for name in self._s3_buckets():
                found.add(("bucket", name))
        except Exception:
            pass
        return found

    # ---- Object Storage (S3) ----

    def _s3(self):
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=f"https://s3.{self.target.region}.scw.cloud",
            region_name=self.target.region,
            aws_access_key_id=self.target.scw_access_key,
            aws_secret_access_key=self.target.scw_secret_key,
        )

    def _s3_buckets(self) -> list:
        return [b["Name"] for b in self._s3().list_buckets().get("Buckets", [])]

    def ensure_bucket(self, target: Target, name: str) -> None:
        s3 = self._s3()
        if name not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
            s3.create_bucket(Bucket=name)

    # ---- Serverless Container ----

    def _ensure_namespace(self, target: Target) -> str:
        ns_name = f"vignemale-{target.app}-{target.env}"
        ns = next((n for n in self._container.list_namespaces_all() if n.name == ns_name), None)
        if ns is None:
            ns = self._container.create_namespace(
                name=ns_name, project_id=target.scw_project_id, tags=_tags(target)
            )
            ns = self._container.wait_for_namespace(namespace_id=ns.id)
        return ns.id

    def deploy_container(
        self, target: Target, name: str, image: str,
        env: Dict[str, str], secret_env: Dict[str, str],
    ) -> str:
        from scaleway.container.v1beta1 import (
            Secret as ContainerSecret, ContainerPrivacy, ContainerProtocol,
        )
        ns_id = self._ensure_namespace(target)
        secrets_list = [ContainerSecret(key=k, value=v) for k, v in secret_env.items()]
        existing = next(
            (c for c in self._container.list_containers_all(namespace_id=ns_id) if c.name == name),
            None,
        )
        if existing is None:
            container = self._container.create_container(
                namespace_id=ns_id, name=name, registry_image=image, port=8080,
                environment_variables=env, secret_environment_variables=secrets_list,
                min_scale=0, max_scale=5, privacy=ContainerPrivacy.PUBLIC,
                protocol=ContainerProtocol.HTTP1, tags=_tags(target),
            )
        else:
            container = self._container.update_container(
                container_id=existing.id, registry_image=image,
                environment_variables=env, secret_environment_variables=secrets_list,
            )
        self._container.deploy_container(container_id=container.id)
        container = self._container.wait_for_container(container_id=container.id)
        domain = getattr(container, "domain_name", "") or ""
        return f"https://{domain}" if domain else ""
