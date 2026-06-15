"""Provider Scaleway : traduit le plan en ressources réelles dans le compte client.

Le SDK `scaleway` est importé PARESSEUSEMENT (extra `vignemale-deploy[scaleway]`) :
le moteur de plan (dry-run) n'en a pas besoin, seul l'APPLY réel l'utilise.

Mapping ressource Vignemale → Scaleway :
  db_instance → Managed Database for PostgreSQL (extension pgvector)
  database    → base logique dans l'instance
  bucket      → Object Storage (bucket S3)
  secret      → Secret Manager
  container   → Serverless Container (image depuis le Container Registry)

Idempotence : chaque ressource est étiquetée `vignemale-app=<app>` +
`vignemale-env=<env>` ; `existing()` les retrouve par tags pour ne pas recréer
(état dans Scaleway = source de vérité unique, cf. docs/phase4-deploy.md).
"""

from typing import Set, Tuple

from .model import Target, Action


def _tags(target: Target) -> list:
    return [f"vignemale-app={target.app}", f"vignemale-env={target.env}"]


class ScalewayProvider:
    """Provider réel. existing()/apply() = tranche APPLY (à venir)."""

    def __init__(self, target: Target):
        if not (target.scw_access_key and target.scw_secret_key and target.scw_project_id):
            raise ValueError(
                "ScalewayProvider exige scw_access_key / scw_secret_key / "
                "scw_project_id sur le Target"
            )
        self.target = target
        # import paresseux : seul l'apply réel a besoin du SDK
        try:
            from scaleway import Client  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise SystemExit(
                "vignemale deploy: SDK Scaleway requis pour l'apply réel — "
                "`pip install 'vignemale-deploy[scaleway]'`"
            ) from e

    def existing(self, target: Target) -> Set[Tuple[str, str]]:
        """Ensemble des (kind, name) déjà présents, retrouvés par tags.

        TODO (tranche apply) : interroger les APIs RDB / Object Storage /
        Secret Manager / Serverless Containers, filtrer par tags _tags(target).
        """
        raise NotImplementedError("lookup Scaleway — tranche apply à venir")

    def apply(self, action: Action) -> None:
        """Crée/met à jour une ressource. TODO (tranche apply)."""
        raise NotImplementedError("apply Scaleway — tranche apply à venir")
