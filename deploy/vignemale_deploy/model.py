"""Types du moteur d'orchestration — données pures, sans dépendance cloud.

Le CLI (mode --local) comme le control plane construisent un `Target`, appellent
le moteur (`engine.build_plan`) et obtiennent un `Plan` : la liste des actions de
réconciliation + les variables d'environnement à poser sur le container.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass(frozen=True)
class Target:
    """Où déployer : une app + un environnement, dans un compte Scaleway CLIENT.

    En dry-run hors-ligne, les credentials sont None (le plan suppose alors que
    rien n'existe encore). Le control plane les fournit (creds délégués du client).
    """
    app: str
    env: str = "prod"
    region: str = "fr-par"                  # fr-par · nl-ams · pl-waw
    image: Optional[str] = None             # ref/digest de l'image d'app (vignemale build)
    scw_access_key: Optional[str] = None
    scw_secret_key: Optional[str] = None
    scw_project_id: Optional[str] = None


@dataclass(frozen=True)
class Resource:
    """Une ressource désirée, indépendante du provider (mais étiquetée Scaleway)."""
    kind: str            # db_instance | database | bucket | secret | container
    name: str            # nom logique (issu du meta)
    provider_type: str   # libellé lisible du service Scaleway visé
    detail: str = ""


@dataclass
class Action:
    op: str              # create | noop | update
    resource: Resource
    reason: str = ""


@dataclass
class Plan:
    target: Target
    actions: List[Action]
    env_vars: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for a in self.actions:
            c[a.op] = c.get(a.op, 0) + 1
        return c
