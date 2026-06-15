"""`vignemale gateway` — construit la table de routage depuis le graphe meta
et démarre la gateway (entrée unique d'une app multi-services déployée).

Pour chaque endpoint, le préfixe littéral de son path (jusqu'au premier
segment dynamique `:`/`*`) est routé vers son service ; l'URL du service vient
de `VIGNEMALE_SERVICE_<NOM>`. Un endpoint `auth=True` rend son préfixe protégé
(auth jouée à l'edge). Le secret partagé : `VIGNEMALE_SERVICE_SECRET`.
"""

import os

from .collect import extract_path


def _literal_prefix(path: str) -> str:
    """Préfixe littéral d'un path (avant le premier :param ou *wildcard)."""
    segs = []
    for seg in (path or "").split("/"):
        if seg.startswith((":", "*")):
            break
        if seg:
            segs.append(seg)
    return "/" + "/".join(segs)


def build_routes(path: str) -> list:
    """(prefix, service, upstream_url, requires_auth) par préfixe d'endpoint."""
    extracted, _ = extract_path(path)
    routes = {}
    for svc in extracted["services"]:
        name = svc["name"]
        env_key = f"VIGNEMALE_SERVICE_{name.upper().replace('-', '_')}"
        upstream = os.environ.get(env_key)
        if not upstream:
            raise SystemExit(
                f"gateway : URL du service '{name}' manquante (pose {env_key})"
            )
        for ep in svc["endpoints"]:
            prefix = _literal_prefix(ep["path"])
            auth = bool(ep.get("auth"))
            if prefix in routes:
                p, s, u, a = routes[prefix]
                routes[prefix] = (p, s, u, a or auth)
            else:
                routes[prefix] = (prefix, name, upstream, auth)
    return list(routes.values())
