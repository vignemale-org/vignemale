"""Provider Scaleway : exécute le plan via le SDK officiel `scaleway`.

Colle fine au-dessus du SDK (cf. docs/phase4-deploy.md §8) :
  rdb.v1        → instance Managed PostgreSQL + bases logiques
  container.v1beta1 → namespace + container + deploy
  Object Storage → S3 pur via boto3 (pas de module SDK)

Idempotence par tags `vignemale-app=<app>` + `vignemale-env=<env>` et lookup
`list_*`. Le mot de passe de l'instance n'étant PAS récupérable après création,
on persiste un petit état local (`~/.vignemale/deploy-state/<app>-<env>.json`) —
le control plane remplacera ce fichier par sa base Postgres.

⚠ Les valeurs marquées « VERIFY » (version d'engine, type de nœud, unité de
volume) sont à confirmer au premier apply réel contre le compte.
"""

import json
import os
import secrets as _secrets
import string
from pathlib import Path
from typing import Dict, Set, Tuple

from .model import Target, DbEndpoint

# Réglages par défaut de l'instance Managed Database (surchargables par env).
_DB_ENGINE = os.environ.get("VIGNEMALE_SCW_DB_ENGINE", "PostgreSQL-15")  # VERIFY
_DB_NODE_TYPE = os.environ.get("VIGNEMALE_SCW_DB_NODE", "DB-DEV-S")       # VERIFY
_DB_VOLUME_GB = int(os.environ.get("VIGNEMALE_SCW_DB_VOLUME_GB", "10"))
_DB_USER = "vignemale"
_STATE_DIR = Path(os.environ.get(
    "VIGNEMALE_STATE_DIR", str(Path.home() / ".vignemale" / "deploy-state")
))


def _tags(target: Target) -> list:
    return [f"vignemale-app={target.app}", f"vignemale-env={target.env}"]


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
            from scaleway.rdb.v1 import RdbV1API
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
        self._rdb = RdbV1API(self._client)
        self._container = ContainerV1Beta1API(self._client)

    # ---- état local (mot de passe DB + IDs) ----

    def _state_path(self) -> Path:
        return _STATE_DIR / f"{self.target.app}-{self.target.env}.json"

    def _load_state(self) -> dict:
        p = self._state_path()
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_state(self, state: dict) -> None:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state, indent=2))

    # ---- lookup (lecture seule, sûr à lancer) ----

    def existing(self, target: Target) -> Set[Tuple[str, str]]:
        found: Set[Tuple[str, str]] = set()
        wanted = set(_tags(target))
        for inst in self._rdb.list_instances_all():
            if wanted.issubset(set(inst.tags or [])):
                found.add(("db_instance", inst.name))
        for ns in self._container.list_namespaces_all():
            for c in self._container.list_containers_all(namespace_id=ns.id):
                if wanted.issubset(set(c.tags or [])):
                    found.add(("container", c.name))
        # buckets : via boto3 (lecture)
        try:
            for name in self._s3_buckets():
                found.add(("bucket", name))
        except Exception:
            pass
        return found

    # ---- Managed Database ----

    def ensure_db_instance(self, target: Target, name: str) -> DbEndpoint:
        state = self._load_state()
        # 1) déjà connu localement → on réutilise (mot de passe en état).
        if state.get("db", {}).get("name") == name and state["db"].get("password"):
            db = state["db"]
            return DbEndpoint(db["instance_id"], db["host"], db["port"], db["user"], db["password"])

        # 2) chercher une instance existante par nom.
        existing = next(
            (i for i in self._rdb.list_instances_all() if i.name == name), None
        )
        if existing is not None:
            raise SystemExit(
                f"vignemale deploy: l'instance « {name} » existe déjà côté Scaleway "
                "mais son mot de passe n'est pas dans l'état local. Restaure l'état "
                "(~/.vignemale/deploy-state) ou supprime l'instance pour repartir net. "
                "(Le control plane stocke cet état dans sa base.)"
            )

        # 3) créer.
        password = _gen_password()
        inst = self._rdb.create_instance(
            engine=_DB_ENGINE,
            user_name=_DB_USER,
            password=password,
            node_type=_DB_NODE_TYPE,
            is_ha_cluster=False,
            disable_backup=False,
            volume_size=_DB_VOLUME_GB * 1000 * 1000 * 1000,  # VERIFY: octets
            backup_same_region=True,
            name=name,
            project_id=target.scw_project_id,
            tags=_tags(target),
        )
        inst = self._rdb.wait_for_instance(instance_id=inst.id)
        ep = (inst.endpoints or [None])[0]
        if ep is None:
            raise SystemExit(
                f"vignemale deploy: instance « {name} » créée mais sans endpoint "
                "— créer un endpoint puis relancer."
            )
        host = getattr(ep, "ip", None) or getattr(getattr(ep, "load_balancer", None), "name", "")
        port = getattr(ep, "port", 5432)
        self._save_state({
            "db": {
                "name": name, "instance_id": inst.id, "host": str(host),
                "port": int(port), "user": _DB_USER, "password": password,
            }
        })
        return DbEndpoint(inst.id, str(host), int(port), _DB_USER, password)

    def ensure_database(self, target: Target, instance_id: str, name: str) -> None:
        exists = any(
            d.name == name
            for d in self._rdb.list_databases_all(instance_id=instance_id)
        )
        if not exists:
            self._rdb.create_database(instance_id=instance_id, name=name)
        # pgvector : à activer par `CREATE EXTENSION vector` (via les migrations
        # de l'app, ou une étape dédiée — voir TODO migrations).

    # ---- Object Storage (S3) ----

    def _s3(self):
        import boto3  # lazy
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
        ns = next(
            (n for n in self._container.list_namespaces_all() if n.name == ns_name), None
        )
        if ns is None:
            ns = self._container.create_namespace(
                name=ns_name, project_id=target.scw_project_id, tags=_tags(target)
            )
            ns = self._container.wait_for_namespace(namespace_id=ns.id)
        return ns.id

    def deploy_container(
        self,
        target: Target,
        name: str,
        image: str,
        env: Dict[str, str],
        secret_env: Dict[str, str],
    ) -> str:
        from scaleway.container.v1beta1 import (
            Secret as ContainerSecret,
            ContainerPrivacy,
            ContainerProtocol,
        )

        ns_id = self._ensure_namespace(target)
        secrets_list = [ContainerSecret(key=k, value=v) for k, v in secret_env.items()]

        existing = next(
            (c for c in self._container.list_containers_all(namespace_id=ns_id)
             if c.name == name), None
        )
        if existing is None:
            container = self._container.create_container(
                namespace_id=ns_id,
                name=name,
                registry_image=image,
                port=8080,
                environment_variables=env,
                secret_environment_variables=secrets_list,
                min_scale=0,
                max_scale=5,
                privacy=ContainerPrivacy.PUBLIC,
                protocol=ContainerProtocol.HTTP1,
                tags=_tags(target),
            )
        else:
            container = self._container.update_container(
                container_id=existing.id,
                registry_image=image,
                environment_variables=env,
                secret_environment_variables=secrets_list,
            )

        self._container.deploy_container(container_id=container.id)
        container = self._container.wait_for_container(container_id=container.id)
        domain = getattr(container, "domain_name", "") or ""
        return f"https://{domain}" if domain else ""
