"""Appels service-à-service : `call("catalog", "get_item", id=7)`.

Le même code marche dans les deux mondes (provider switch, façon Encore) :

- **local** (`vignemale run` d'un dossier) : tous les services sont dans le
  process → l'appel est un appel de fonction direct (zéro HTTP), avec la
  validation Pydantic et les erreurs habituelles ;
- **déployé** : `VIGNEMALE_SERVICE_<NOM>` (posé par le deploy) donne l'URL du
  service → l'appel part en HTTP sur la route interne `/__vignemale/call/…`,
  **signé** (HMAC, secret partagé `VIGNEMALE_SERVICE_SECRET`), avec
  propagation du contexte : `traceparent` (W3C, nouveau span) et les données
  d'auth de la requête entrante (`x-vignemale-auth-data`) — les appels
  internes sont de confiance, ils ne repassent pas par l'auth handler.

    from vignemale import api, call

    @api(method="POST", path="/orders")
    def create_order(body: Order) -> dict:
        item = call("catalog", "get_item", id=body.item_id)
        ...
"""

import hashlib
import hmac as hmac_mod
import json
import os
import secrets as pysecrets
import time
import urllib.error
import urllib.request

from .api import APIError, _endpoints, _request_ctx
from . import service as _service_mod


def call(service: str, endpoint: str, body=None, **params):
    """Appelle `endpoint` du service `service`. Renvoie la réponse décodée.

    `params` = paramètres de chemin de l'endpoint cible ; `body` = corps JSON.
    Les erreurs remontent en `APIError` (même contrat que les appels HTTP).
    """
    env_key = f"VIGNEMALE_SERVICE_{service.upper().replace('-', '_')}"
    base_url = os.environ.get(env_key)
    if not base_url:
        return _call_local(service, endpoint, body, params)
    return _call_http(base_url, service, endpoint, body, params)


# --- mode local : tous les services dans le process, appel direct ---


def _call_local(service: str, endpoint: str, body, params):
    modules = [m for (n, m) in _service_mod._services if n == service]

    def in_service(module: str) -> bool:
        # le module de l'endpoint = celui du Service, ou un sous-module
        # (dossier-service : Service("catalog") dans catalog/__init__.py,
        # endpoints dans catalog/items.py → module "catalog.items")
        return any(module == m or module.startswith(m + ".") for m in modules)

    for name, _method, _path, wrapper, stream, *_rest in _endpoints:
        if name != endpoint:
            continue
        if modules and not in_service(wrapper.__module__):
            continue
        if stream:
            raise APIError(
                "unimplemented", "call() ne supporte pas les endpoints streaming"
            )
        kwargs = dict(params)
        if body is not None:
            kwargs["body"] = body if not hasattr(body, "model_dump") else body.model_dump()
        ctx = _request_ctx.get() or {}
        kwargs["auth"] = ctx.get("auth")  # propagation locale (filtrée si non déclarée)
        kwargs["headers"] = {"traceparent": ctx.get("traceparent") or ""}
        kwargs["query"] = {}
        return wrapper(**kwargs)
    raise APIError("not_found", f"endpoint {service}.{endpoint} introuvable")


# --- mode déployé : HTTP signé sur la route interne ---


def _sign(secret: str, date: str, caller: str, endpoint: str, payload: bytes) -> str:
    body_hash = hashlib.sha256(payload).hexdigest()
    msg = f"{date}\n{caller}\n{endpoint}\n{body_hash}"
    return hmac_mod.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _child_traceparent(ctx: dict) -> str:
    """Propage le trace-id entrant avec un nouveau span-id (W3C)."""
    incoming = ctx.get("traceparent") or ""
    parts = incoming.split("-")
    trace_id = parts[1] if len(parts) >= 4 and len(parts[1]) == 32 else pysecrets.token_hex(16)
    return f"00-{trace_id}-{pysecrets.token_hex(8)}-01"


def _call_http(base_url: str, service: str, endpoint: str, body, params):
    secret = os.environ.get("VIGNEMALE_SERVICE_SECRET")
    if not secret:
        raise APIError(
            "internal",
            "VIGNEMALE_SERVICE_SECRET requis pour les appels inter-services",
        )
    if body is not None and hasattr(body, "model_dump"):
        body = body.model_dump()
    payload = json.dumps(
        {"params": {k: str(v) for k, v in params.items()}, "body": body}
    ).encode()
    caller = os.environ.get("VIGNEMALE_SERVICE_NAME", "unknown")
    date = str(int(time.time()))
    ctx = _request_ctx.get() or {}
    headers = {
        "content-type": "application/json",
        "x-vignemale-date": date,
        "x-vignemale-caller": caller,
        "x-vignemale-signature": _sign(secret, date, caller, endpoint, payload),
        "traceparent": _child_traceparent(ctx),
    }
    if ctx.get("auth") is not None:
        headers["x-vignemale-auth-data"] = json.dumps(ctx["auth"])

    url = f"{base_url.rstrip('/')}/__vignemale/call/{endpoint}"
    req = urllib.request.Request(url, data=payload, headers=headers)
    timeout = float(os.environ.get("VIGNEMALE_CALL_TIMEOUT", "30"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            raise APIError(
                err.get("code", "internal"), err.get("message", "?"), err.get("details")
            ) from None
        except (ValueError, KeyError):
            raise APIError("internal", f"appel {service}.{endpoint}: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise APIError(
            "unavailable", f"service {service} injoignable: {e.reason}"
        ) from None
