"""`vignemale gateway` — builds the routing table from the meta graph
and starts the gateway (single entry point of a deployed multi-service app).

For each endpoint, the literal prefix of its path (up to the first
dynamic segment `:`/`*`) is routed to its service; the service URL comes
from `VIGNEMALE_SERVICE_<NAME>`. An `auth=True` endpoint makes its prefix
protected (auth enforced at the edge). The shared secret: `VIGNEMALE_SERVICE_SECRET`.
"""

import os

from .collect import extract_path


def _literal_prefix(path: str) -> str:
    """Literal prefix of a path (before the first :param or *wildcard)."""
    segs = []
    for seg in (path or "").split("/"):
        if seg.startswith((":", "*")):
            break
        if seg:
            segs.append(seg)
    return "/" + "/".join(segs)


def build_routes(path: str) -> list:
    """(prefix, service, upstream_url, requires_auth) per endpoint prefix."""
    extracted, _ = extract_path(path)
    routes = {}
    for svc in extracted["services"]:
        name = svc["name"]
        env_key = f"VIGNEMALE_SERVICE_{name.upper().replace('-', '_')}"
        upstream = os.environ.get(env_key)
        if not upstream:
            raise SystemExit(
                f"gateway: URL of service '{name}' missing (set {env_key})"
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
