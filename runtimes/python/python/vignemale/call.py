"""Service-to-service calls: `call("catalog", "get_item", id=7)`.

The same code works in both worlds (provider switch, Encore style):

- **local** (`vignemale run` of a directory): all services live in the
  process → the call is a direct function call (zero HTTP), with the usual
  Pydantic validation and errors;
- **deployed**: `VIGNEMALE_SERVICE_<NAME>` (set by the deploy) gives the
  service's URL → the call goes over HTTP on the internal route
  `/__vignemale/call/…`, **signed** (HMAC, shared secret
  `VIGNEMALE_SERVICE_SECRET`), with context propagation: `traceparent` (W3C,
  new span) and the incoming request's auth data (`x-vignemale-auth-data`) —
  internal calls are trusted, they do not go through the auth handler again.

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
    """Calls `endpoint` of service `service`. Returns the decoded response.

    `params` = path parameters of the target endpoint; `body` = JSON body.
    Errors surface as `APIError` (same contract as HTTP calls).
    """
    env_key = f"VIGNEMALE_SERVICE_{service.upper().replace('-', '_')}"
    base_url = os.environ.get(env_key)
    if not base_url:
        return _call_local(service, endpoint, body, params)
    return _call_http(base_url, service, endpoint, body, params)


# --- local mode: all services in the process, direct call ---


def _call_local(service: str, endpoint: str, body, params):
    modules = [m for (n, m) in _service_mod._services if n == service]

    def in_service(module: str) -> bool:
        # the endpoint's module = the Service's module, or a submodule
        # (directory-service: Service("catalog") in catalog/__init__.py,
        # endpoints in catalog/items.py → module "catalog.items")
        return any(module == m or module.startswith(m + ".") for m in modules)

    for name, _method, _path, wrapper, stream, *_rest in _endpoints:
        if name != endpoint:
            continue
        if modules and not in_service(wrapper.__module__):
            continue
        if stream:
            raise APIError(
                "unimplemented", "call() does not support streaming endpoints"
            )
        kwargs = dict(params)
        if body is not None:
            kwargs["body"] = body if not hasattr(body, "model_dump") else body.model_dump()
        ctx = _request_ctx.get() or {}
        kwargs["auth"] = ctx.get("auth")  # local propagation (filtered out if not declared)
        kwargs["headers"] = {"traceparent": ctx.get("traceparent") or ""}
        kwargs["query"] = {}
        return wrapper(**kwargs)
    raise APIError("not_found", f"endpoint {service}.{endpoint} not found")


# --- deployed mode: signed HTTP on the internal route ---


def _sign(
    secret: str, date: str, caller: str, endpoint: str, payload: bytes, auth_data: bytes
) -> str:
    # auth_data (= x-vignemale-auth-data header) included in the signature, the
    # way Encore binds the propagated identity: prevents forging an identity
    # after the fact.
    body_hash = hashlib.sha256(payload).hexdigest()
    auth_hash = hashlib.sha256(auth_data).hexdigest()
    msg = f"{date}\n{caller}\n{endpoint}\n{body_hash}\n{auth_hash}"
    return hmac_mod.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _child_traceparent(ctx: dict) -> str:
    """Propagates the incoming trace-id with a new span-id (W3C)."""
    incoming = ctx.get("traceparent") or ""
    parts = incoming.split("-")
    trace_id = parts[1] if len(parts) >= 4 and len(parts[1]) == 32 else pysecrets.token_hex(16)
    return f"00-{trace_id}-{pysecrets.token_hex(8)}-01"


def _call_http(base_url: str, service: str, endpoint: str, body, params):
    secret = os.environ.get("VIGNEMALE_SERVICE_SECRET")
    if not secret:
        raise APIError(
            "internal",
            "VIGNEMALE_SERVICE_SECRET required for inter-service calls",
        )
    if body is not None and hasattr(body, "model_dump"):
        body = body.model_dump()
    payload = json.dumps(
        {"params": {k: str(v) for k, v in params.items()}, "body": body}
    ).encode()
    caller = os.environ.get("VIGNEMALE_SERVICE_NAME", "unknown")
    date = str(int(time.time()))
    ctx = _request_ctx.get() or {}
    # the propagated identity must be signed exactly as the header (empty if absent)
    auth_data = json.dumps(ctx["auth"]) if ctx.get("auth") is not None else ""
    headers = {
        "content-type": "application/json",
        "x-vignemale-date": date,
        "x-vignemale-caller": caller,
        "x-vignemale-signature": _sign(
            secret, date, caller, endpoint, payload, auth_data.encode()
        ),
        "traceparent": _child_traceparent(ctx),
    }
    if auth_data:
        headers["x-vignemale-auth-data"] = auth_data
    # private peer (services topology): Scaleway invocation token
    token = os.environ.get("VIGNEMALE_CONTAINER_TOKEN")
    if token:
        headers["X-Auth-Token"] = token

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
            raise APIError("internal", f"call {service}.{endpoint}: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise APIError(
            "unavailable", f"service {service} unreachable: {e.reason}"
        ) from None
