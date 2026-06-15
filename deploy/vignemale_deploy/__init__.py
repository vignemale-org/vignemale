"""vignemale-deploy — moteur d'orchestration (provision + deploy Scaleway).

Réutilisable par le CLI (`vignemale deploy --local`) et par le control plane
Vignemale Cloud. Le moteur de plan (dry-run) n'a aucune dépendance ; l'apply réel
utilise le SDK Scaleway (extra `[scaleway]`).

    from vignemale_deploy import Target, build_plan, render
    plan = build_plan(meta, Target(app="shop", env="prod"))
    print(render(plan))
"""

from .model import Target, Resource, Action, Plan
from .engine import build_plan, desired_resources, container_env
from .render import render

__all__ = [
    "Target",
    "Resource",
    "Action",
    "Plan",
    "build_plan",
    "desired_resources",
    "container_env",
    "render",
]
