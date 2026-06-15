"""vignemale-deploy — moteur d'orchestration (provision + deploy Scaleway).

Réutilisable par le CLI (`vignemale deploy --local`) et par le control plane
Vignemale Cloud. Le moteur de plan (dry-run) n'a aucune dépendance ; l'apply réel
utilise le SDK Scaleway + boto3 (extra `[scaleway]`).

    from vignemale_deploy import Target, build_plan, render
    plan = build_plan(meta, Target(app="shop", env="prod"))
    print(render(plan))

    from vignemale_deploy import ScalewayProvider, apply_plan
    dep = apply_plan(meta, target, ScalewayProvider(target), on_progress=print)
"""

from .model import Target, Resource, Action, Plan, DbEndpoint, Deployment
from .engine import build_plan, desired_resources, container_env
from .render import render
from .apply import apply_plan, build_runtime_env, Provider

__all__ = [
    "Target",
    "Resource",
    "Action",
    "Plan",
    "DbEndpoint",
    "Deployment",
    "build_plan",
    "desired_resources",
    "container_env",
    "render",
    "apply_plan",
    "build_runtime_env",
    "Provider",
]


def __getattr__(name):
    # Import paresseux : ScalewayProvider tire le SDK seulement à l'usage.
    if name == "ScalewayProvider":
        from .scaleway import ScalewayProvider

        return ScalewayProvider
    raise AttributeError(name)
