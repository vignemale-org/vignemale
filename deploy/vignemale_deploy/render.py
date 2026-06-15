"""Rendu lisible d'un `Plan`, façon `terraform plan`."""

from .model import Plan

_SYM = {"create": "+", "noop": "=", "update": "~"}


def render(plan: Plan) -> str:
    t = plan.target
    out = [
        f"Plan de déploiement — app « {t.app} » · env « {t.env} » · région {t.region}",
        "",
        "Ressources (compte Scaleway du client) :",
    ]
    for a in plan.actions:
        out.append(f"  {_SYM.get(a.op, '?')} {a.resource.provider_type} : {a.resource.name}")
        if a.resource.detail:
            out.append(f"      {a.resource.detail}")

    out += ["", "Variables d'environnement posées sur le container (provider switch) :"]
    for k, v in plan.env_vars.items():
        out.append(f"  {k} = {v}")

    if plan.warnings:
        out += ["", "Avertissements :"]
        out += [f"  ! {w}" for w in plan.warnings]

    counts = plan.counts()
    label = {"create": "à créer", "noop": "inchangée(s)", "update": "à modifier"}
    summary = ", ".join(f"{n} {label.get(op, op)}" for op, n in sorted(counts.items()))
    out += ["", f"Résumé : {summary or 'rien à faire'}."]
    return "\n".join(out)
