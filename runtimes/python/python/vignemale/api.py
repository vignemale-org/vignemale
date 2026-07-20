"""Vignemale's API SDK: the `@api` decorator (Pydantic-typed) + `serve()`.

    from pydantic import BaseModel
    from vignemale.api import api, serve

    class ChatRequest(BaseModel):
        prompt: str

    @api(method="POST", path="/chat")
    def chat(body: ChatRequest) -> ChatReply:    # validated at runtime + extracted statically
        ...

    serve("127.0.0.1:8080")

A handler receives what its signature declares: the path parameters
(`/notes/:id` → `id`), `body` (parsed JSON / Pydantic model), `query` (dict of
query string parameters) and `headers` (dict, lowercase names).

Errors follow the Encore contract: `{code, message, details}` body,
gRPC-style codes mapped onto HTTP statuses (see `APIError`).
"""

import contextvars
import errno
import functools
import inspect
import json
import os
from typing import Callable, get_type_hints

from . import _core

# Registry of declared endpoints (filled by the decorator when the app is imported).
_endpoints: list = []

# Registry of declared static directories (served by the Rust core).
_static_routes: list = []


def static_files(*, path: str, dir: str, spa: bool = False, not_found: str = None) -> None:
    """Serves a directory of static files **from the Rust core** — zero
    Python code executed per request (mirror of Encore's `api.static`).

        static_files(path="/assets", dir="./public")     # /assets/logo.png …
        static_files(path="/", dir="./out", spa=True)    # frontend as fallback:
        # any route unknown to the API returns index.html (client-side routing —
        # Next.js `output: 'export'`, Vite, React Router…)

    Relative paths are resolved against the declaring file.
    """
    import inspect as _inspect

    base = os.path.dirname(
        os.path.abspath(_inspect.stack()[1].frame.f_globals.get("__file__", "."))
    )

    def resolve(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))

    directory = resolve(dir)
    nf = not_found or (os.path.join(directory, "index.html") if spa else None)
    fallback = spa or path.rstrip("/") == ""
    _static_routes.append(
        (path.rstrip("/") or "/", directory, nf and resolve(nf), fallback)
    )

# Context of the in-flight request (set by the wrapper, read by `call()` to
# propagate trace and auth to service-to-service calls).
_request_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "vignemale_request_ctx", default=None
)

# Error codes (Encore / gRPC style) → HTTP status.
_CODE_TO_STATUS = {
    "canceled": 499,
    "unknown": 500,
    "invalid_argument": 400,
    "deadline_exceeded": 504,
    "not_found": 404,
    "already_exists": 409,
    "permission_denied": 403,
    "resource_exhausted": 429,
    "failed_precondition": 400,
    "aborted": 409,
    "out_of_range": 400,
    "unimplemented": 501,
    "internal": 500,
    "unavailable": 503,
    "data_loss": 500,
    "unauthenticated": 401,
}
_STATUS_TO_CODE = {
    400: "invalid_argument",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    409: "already_exists",
    429: "resource_exhausted",
    499: "canceled",
    500: "internal",
    501: "unimplemented",
    503: "unavailable",
    504: "deadline_exceeded",
}


class APIError(Exception):
    """API error following the Encore contract: `{code, message, details}` + HTTP status.

        raise APIError("not_found", "note not found")
        raise APIError.not_found("note not found")            # shortcut
        raise APIError.permission_denied("admin only", details={"role": role})
    """

    def __init__(self, code: str, message: str, details=None):
        status = _CODE_TO_STATUS.get(code)
        if status is None:
            raise ValueError(f"unknown API error code: {code!r}")
        self.code = code
        self.vignemale_status = status
        self.vignemale_body = json.dumps(
            {"code": code, "message": message, "details": details}
        )
        super().__init__(f"{code}: {message}")


def _add_shortcut(code: str) -> None:
    def shortcut(cls, message: str, details=None):
        return cls(code, message, details)

    shortcut.__name__ = code
    shortcut.__doc__ = f"Shortcut for APIError({code!r}, …)."
    setattr(APIError, code, classmethod(shortcut))


for _code in _CODE_TO_STATUS:
    _add_shortcut(_code)


class HTTPError(APIError):
    """Error by HTTP status — sugar on top of `APIError`:

        raise HTTPError(404, "not found")
        # ≡ APIError("not_found", "not found")
    """

    def __init__(self, status: int, detail=None):
        code = _STATUS_TO_CODE.get(
            int(status), "internal" if int(status) >= 500 else "unknown"
        )
        if isinstance(detail, str) or detail is None:
            message, details = detail or f"HTTP {status}", None
        else:
            message, details = f"HTTP {status}", detail
        super().__init__(code, message, details)
        self.vignemale_status = int(status)  # the requested status wins over the code


def _pydantic_model(tp):
    """Returns `tp` if it is a Pydantic model, None otherwise."""
    try:
        from pydantic import BaseModel

        return tp if isinstance(tp, type) and issubclass(tp, BaseModel) else None
    except Exception:
        return None


def _to_jsonable(v):
    """Serializes recursively (nested Pydantic models included)."""
    if _pydantic_model(type(v)) is not None:
        return v.model_dump()
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


# --- authentication (Encore style: ONE auth handler per app) ---

_auth_handler = None
_auth_required: list = []  # names of protected endpoints (validated at serve)


def auth_handler(func: Callable) -> Callable:
    """Declares THE app's authentication handler (only one).

    Receives the token (`Authorization: Bearer …`, or `?token=` for clients
    that cannot set a header, e.g. EventSource). Returns the auth data (dict
    or Pydantic model) if the token is valid, `None` otherwise
    (→ 401 `unauthenticated`). Endpoints `@api(..., auth=True)` receive this
    data in the `auth` parameter if they declare it.

        @auth_handler
        def check(token):
            user = verify(token)
            return {"user_id": user.id} if user else None
    """
    global _auth_handler
    if _auth_handler is not None:
        raise RuntimeError("an auth_handler is already declared (only one per app)")
    _auth_handler = func
    return func


def api(
    *,
    method: str,
    path: str,
    stream: bool = False,
    auth: bool = False,
    expose: bool = True,
    timeout: float = None,
    body_limit: int = None,
) -> Callable:
    """Declares a function as an HTTP endpoint.

    - If the `body` parameter is annotated with a Pydantic model, the request
      is **validated** (and coerced) before the handler is called (otherwise
      → 400 `invalid_argument` with the Pydantic detail).
    - If the return value is a Pydantic model, it is serialized automatically.
    - `stream=True`: the handler receives `stream` and pushes fragments (SSE).
    - `auth=True`: the request first goes through the app's `@auth_handler`
      (otherwise → 401 `unauthenticated`); the handler receives `auth` if it
      declares it.
    - `expose=False` (PRIVATE): the endpoint is NOT exposed publicly — it is
      only reachable service-to-service via `call()` (signed internal route).
      An external call gets a 404. Default: exposed (`True`).
    - `timeout` (seconds): beyond it → 504 `deadline_exceeded` (the handler
      finishes in the background, its logs are kept). Default:
      `VIGNEMALE_REQUEST_TIMEOUT` (30 s; 0 = disabled). Ignored when streaming.
    - `body_limit` (bytes): beyond it → 413 `resource_exhausted`. Default:
      `VIGNEMALE_MAX_BODY` (10 MiB).
    """

    def decorator(func: Callable) -> Callable:
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        body_model = _pydantic_model(hints.get("body"))

        sig = inspect.signature(func)
        accepts_var_kwargs = any(
            p.kind == p.VAR_KEYWORD for p in sig.parameters.values()
        )
        accepted = set(sig.parameters)
        body_required = (
            "body" in sig.parameters
            and sig.parameters["body"].default is inspect.Parameter.empty
        )

        @functools.wraps(func)
        def wrapper(**kwargs):
            # request context (trace + auth), BEFORE the filtering — `call()`
            # uses it to propagate to service-to-service calls
            ctx = {
                "traceparent": (kwargs.get("headers") or {}).get("traceparent"),
                "auth": kwargs.get("auth"),
            }
            # the runtime provides everything (params, query, headers, body, auth);
            # we only pass on what the handler's signature declares.
            # Authentication itself is performed by the CORE, before the call.
            if not accepts_var_kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k in accepted}
            if body_required and "body" not in kwargs:
                raise APIError("invalid_argument", "request body required")
            if body_model is not None and "body" in kwargs:
                from pydantic import ValidationError

                try:
                    kwargs["body"] = body_model.model_validate(kwargs["body"])
                except ValidationError as e:
                    raise APIError(
                        "invalid_argument",
                        "invalid request",
                        details=json.loads(e.json()),
                    ) from None
            ctx_token = _request_ctx.set(ctx)
            try:
                result = func(**kwargs)
            finally:
                _request_ctx.reset(ctx_token)
            return _to_jsonable(result)

        if auth:
            _auth_required.append(func.__name__)
        _endpoints.append(
            (func.__name__, method.upper(), path, wrapper, stream, auth, timeout, body_limit, expose)
        )
        return func  # we return the original typed function (for pyright)

    return decorator


def _auth_adapter(token: str):
    """Normalizes the auth handler's return value before handing it to the core."""
    data = _auth_handler(token)
    if data is not None and _pydantic_model(type(data)) is not None:
        data = data.model_dump()
    return data


def _gateway_routes() -> list:
    """Builds the gateway routes from the loaded endpoints + the services'
    URLs (VIGNEMALE_SERVICE_<NAME>, set by the deploy at discovery time).

    Returns a list of (prefix, service, upstream_url, requires_auth). The prefix
    is the static part of the path (up to the 1st parameter segment). Private
    endpoints (expose=False) are never routed publicly.
    """
    from .service import _services

    def service_of(module: str):
        for n, m in _services:
            if module == m or module.startswith(m + "."):
                return n
        return None

    def static_prefix(path: str) -> str:
        segs = []
        for seg in path.strip("/").split("/"):
            if not seg or seg[0] in (":", "{"):
                break
            segs.append(seg)
        return "/" + "/".join(segs)

    # (prefix, service, url) → requires_auth (OR of the endpoints under this prefix)
    routes: dict = {}
    for name, method, path, wrapper, stream, auth, timeout, body_limit, expose in _endpoints:
        if not expose:
            continue  # private: never exposed via the gateway
        svc = service_of(wrapper.__module__)
        if svc is None:
            continue
        url = os.environ.get("VIGNEMALE_SERVICE_" + svc.upper().replace("-", "_"))
        if not url:
            continue  # service URL unknown → not routable
        key = (static_prefix(path), svc, url)
        routes[key] = routes.get(key, False) or bool(auth)
    return [(pref, svc, url, req) for (pref, svc, url), req in routes.items()]


def print_banner() -> None:
    """Print the one-line startup banner once, at server startup.

    Called from the entry points (`vignemale run`, `python -m vignemale`) — NOT
    from `serve()`, so it is not repeated once per worker in multi-process mode.
    """
    try:
        version = f" v{_core.version()}"
    except Exception:
        version = ""
    print(f"▲ vignemale{version}  ·  infrastructure from code", flush=True)


def _port_in_use_error(addr: str) -> SystemExit:
    host, _, port = addr.rpartition(":")
    return SystemExit(
        f"vignemale: {addr} is already in use — another process is listening on "
        f"this port.\n"
        f"  Find it with:       lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
        f"  Or pick another:    --addr {host}:{int(port) + 1 if port.isdigit() else '<port>'}"
    )


def _check_port_free(addr: str, reuse_port: bool) -> None:
    """Fail fast, with a clear error, if the address is already taken.

    The core binds on a background thread, *after* the startup banner has been
    printed — without this check a busy port surfaces as a raw RuntimeError
    right below a banner claiming the server is up.
    """
    import socket

    host, _, port_s = addr.rpartition(":")
    try:
        port = int(port_s)
    except ValueError:
        return  # unusual address format: let the core report it
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if reuse_port and hasattr(socket, "SO_REUSEPORT"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        probe.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise _port_in_use_error(addr) from None
        return  # other bind errors (bad host, perms…): let the core report them
    finally:
        probe.close()


def serve_gateway(routes: list, addr: str = "127.0.0.1:8080", reuse_port: bool = False) -> None:
    """Starts the GATEWAY: the single entry point of a deployed multi-service app.

    `routes`: list of (prefix, service, upstream_url, requires_auth). Public
    traffic is authenticated at the edge (via the loaded `@auth_handler`) then
    forwarded as signed HTTP (svcauth) to the right service. Used by
    `vignemale gateway`; the shared secret comes from VIGNEMALE_SERVICE_SECRET.
    """
    _check_port_free(addr, reuse_port)
    print(f"vignemale: gateway on http://{addr} ({len(routes)} service(s))", flush=True)
    try:
        _core.serve_gateway(
            routes, addr, _auth_adapter if _auth_handler is not None else None, reuse_port
        )
    except KeyboardInterrupt:
        print("vignemale: gateway stopped", flush=True)
    except RuntimeError as exc:
        if "address already in use" in str(exc).lower():
            raise _port_in_use_error(addr) from None
        raise


def _endpoints_to_serve() -> list:
    """Endpoints to be served by THIS container.

    "One container per service" topology: the deploy sets `VIGNEMALE_SERVICE_NAME`
    → we ONLY serve this service's endpoints (the others remain reachable via
    `call()` HTTP to their container). Without this variable (mono) → everything
    is served.
    """
    svc = os.environ.get("VIGNEMALE_SERVICE_NAME")
    if not svc:
        return list(_endpoints)
    from .service import _services

    modules = [m for (n, m) in _services if n == svc]
    if not modules:
        # unknown service name (bad config): we serve everything rather than an
        # empty container, while flagging it.
        print(
            f"vignemale: VIGNEMALE_SERVICE_NAME={svc!r} does not match any "
            "declared Service() — all endpoints are served.",
            flush=True,
        )
        return list(_endpoints)

    def in_service(module: str) -> bool:
        return any(module == m or module.startswith(m + ".") for m in modules)

    return [e for e in _endpoints if in_service(e[3].__module__)]  # e[3] = wrapper


def serve(addr: str = "127.0.0.1:8080", reuse_port: bool = False) -> None:
    """Starts the HTTP server.

    Stops **gracefully** on Ctrl-C or SIGTERM (containers): healthz switches
    to 503 `shutting_down`, no more connections are accepted, in-flight
    requests finish (bounded by `VIGNEMALE_SHUTDOWN_TIMEOUT`, 10 s).
    """
    endpoints = _endpoints_to_serve()
    # auth validation restricted to the endpoints actually served (e[5] = auth)
    protected = [e[0] for e in endpoints if e[5]]
    if protected and _auth_handler is None:
        raise SystemExit(
            "vignemale: protected endpoint(s) without a declared @auth_handler: "
            + ", ".join(protected)
        )
    import signal as _signal

    def _sigterm(*_args):
        raise KeyboardInterrupt  # same graceful shutdown path as Ctrl-C

    try:
        _signal.signal(_signal.SIGTERM, _sigterm)
    except ValueError:
        pass  # not in the main thread (tests…): no SIGTERM handling then

    _check_port_free(addr, reuse_port)
    svc = os.environ.get("VIGNEMALE_SERVICE_NAME")
    suffix = f' (service "{svc}")' if svc else ""
    print(f"vignemale: {len(endpoints)} endpoint(s) on http://{addr}{suffix}", flush=True)
    try:
        _core.serve(
            endpoints,
            addr,
            _auth_adapter if _auth_handler is not None else None,
            list(_static_routes),
            reuse_port,
        )
    except KeyboardInterrupt:
        print("vignemale: stopped", flush=True)
    except RuntimeError as exc:
        if "address already in use" in str(exc).lower():
            raise _port_in_use_error(addr) from None
        raise
