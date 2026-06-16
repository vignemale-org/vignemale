"""SDK API de Vignemale : le décorateur `@api` (typé Pydantic) + `serve()`.

    from pydantic import BaseModel
    from vignemale.api import api, serve

    class ChatRequest(BaseModel):
        prompt: str

    @api(method="POST", path="/chat")
    def chat(body: ChatRequest) -> ChatReply:    # validé au runtime + extrait en statique
        ...

    serve("127.0.0.1:8080")

Un handler reçoit ce que sa signature déclare : les paramètres de chemin
(`/notes/:id` → `id`), `body` (JSON parsé / modèle Pydantic), `query` (dict des
paramètres de query string) et `headers` (dict, noms en minuscules).

Les erreurs suivent le contrat Encore : corps `{code, message, details}`, codes
gRPC-style mappés sur les statuts HTTP (cf. `APIError`).
"""

import contextvars
import functools
import inspect
import json
import os
from typing import Callable, get_type_hints

from . import _core

# Registre des endpoints déclarés (rempli par le décorateur à l'import de l'app).
_endpoints: list = []

# Registre des dossiers statiques déclarés (servis par le core Rust).
_static_routes: list = []


def static_files(*, path: str, dir: str, spa: bool = False, not_found: str = None) -> None:
    """Sert un dossier de fichiers statiques **depuis le core Rust** — zéro
    code Python exécuté par requête (miroir d'`api.static` d'Encore).

        static_files(path="/assets", dir="./public")     # /assets/logo.png …
        static_files(path="/", dir="./out", spa=True)    # front en fallback :
        # toute route inconnue de l'API renvoie index.html (routing client —
        # Next.js `output: 'export'`, Vite, React Router…)

    Les chemins relatifs sont résolus par rapport au fichier qui déclare.
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

# Contexte de la requête en cours (posé par le wrapper, lu par `call()` pour
# propager trace et auth aux appels service-à-service).
_request_ctx: contextvars.ContextVar = contextvars.ContextVar(
    "vignemale_request_ctx", default=None
)

# Codes d'erreur (façon Encore / gRPC) → statut HTTP.
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
    """Erreur API au contrat Encore : `{code, message, details}` + statut HTTP.

        raise APIError("not_found", "note introuvable")
        raise APIError.not_found("note introuvable")          # raccourci
        raise APIError.permission_denied("réservé à l'admin", details={"role": role})
    """

    def __init__(self, code: str, message: str, details=None):
        status = _CODE_TO_STATUS.get(code)
        if status is None:
            raise ValueError(f"code d'erreur API inconnu: {code!r}")
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
    shortcut.__doc__ = f"Raccourci pour APIError({code!r}, …)."
    setattr(APIError, code, classmethod(shortcut))


for _code in _CODE_TO_STATUS:
    _add_shortcut(_code)


class HTTPError(APIError):
    """Erreur par statut HTTP — sucre au-dessus d'`APIError` :

        raise HTTPError(404, "introuvable")
        # ≡ APIError("not_found", "introuvable")
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
        self.vignemale_status = int(status)  # le statut demandé prime sur le code


def _pydantic_model(tp):
    """Renvoie `tp` si c'est un modèle Pydantic, sinon None."""
    try:
        from pydantic import BaseModel

        return tp if isinstance(tp, type) and issubclass(tp, BaseModel) else None
    except Exception:
        return None


def _to_jsonable(v):
    """Sérialise récursivement (modèles Pydantic imbriqués compris)."""
    if _pydantic_model(type(v)) is not None:
        return v.model_dump()
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    return v


# --- authentification (façon Encore : UN auth handler par app) ---

_auth_handler = None
_auth_required: list = []  # noms des endpoints protégés (validation au serve)


def auth_handler(func: Callable) -> Callable:
    """Déclare LE handler d'authentification de l'app (un seul).

    Reçoit le token (`Authorization: Bearer …`, ou `?token=` pour les clients
    qui ne peuvent pas poser d'en-tête, ex. EventSource). Renvoie les données
    d'auth (dict ou modèle Pydantic) si le token est valide, `None` sinon
    (→ 401 `unauthenticated`). Les endpoints `@api(..., auth=True)` reçoivent
    ces données dans le paramètre `auth` s'ils le déclarent.

        @auth_handler
        def check(token):
            user = verify(token)
            return {"user_id": user.id} if user else None
    """
    global _auth_handler
    if _auth_handler is not None:
        raise RuntimeError("un auth_handler est déjà déclaré (un seul par app)")
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
    """Déclare une fonction comme endpoint HTTP.

    - Si le paramètre `body` est annoté avec un modèle Pydantic, la requête est
      **validée** (et coercée) avant l'appel du handler (sinon → 400
      `invalid_argument` avec le détail Pydantic).
    - Si le retour est un modèle Pydantic, il est sérialisé automatiquement.
    - `stream=True` : le handler reçoit `stream` et pousse des fragments (SSE).
    - `auth=True` : la requête passe d'abord par le `@auth_handler` de l'app
      (sinon → 401 `unauthenticated`) ; le handler reçoit `auth` s'il le déclare.
    - `expose=False` (PRIVATE) : l'endpoint n'est PAS exposé publiquement — il
      n'est joignable qu'en service-à-service via `call()` (route interne signée).
      Un appel externe reçoit 404. Défaut : exposé (`True`).
    - `timeout` (secondes) : au-delà → 504 `deadline_exceeded` (le handler
      finit en arrière-plan, ses logs sont conservés). Défaut :
      `VIGNEMALE_REQUEST_TIMEOUT` (30 s ; 0 = désactivé). Ignoré en streaming.
    - `body_limit` (octets) : au-delà → 413 `resource_exhausted`. Défaut :
      `VIGNEMALE_MAX_BODY` (10 Mio).
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
            # contexte de requête (trace + auth), AVANT le filtrage — `call()`
            # s'en sert pour propager aux appels service-à-service
            ctx = {
                "traceparent": (kwargs.get("headers") or {}).get("traceparent"),
                "auth": kwargs.get("auth"),
            }
            # le runtime fournit tout (params, query, headers, body, auth) ;
            # on ne transmet que ce que la signature du handler déclare.
            # L'authentification elle-même est jouée par le CORE, avant l'appel.
            if not accepts_var_kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k in accepted}
            if body_required and "body" not in kwargs:
                raise APIError("invalid_argument", "corps de requête requis")
            if body_model is not None and "body" in kwargs:
                from pydantic import ValidationError

                try:
                    kwargs["body"] = body_model.model_validate(kwargs["body"])
                except ValidationError as e:
                    raise APIError(
                        "invalid_argument",
                        "requête invalide",
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
        return func  # on renvoie la fonction typée d'origine (pour pyright)

    return decorator


def _auth_adapter(token: str):
    """Normalise le retour de l'auth handler avant le passage au core."""
    data = _auth_handler(token)
    if data is not None and _pydantic_model(type(data)) is not None:
        data = data.model_dump()
    return data


def serve_gateway(routes: list, addr: str = "127.0.0.1:8080", reuse_port: bool = False) -> None:
    """Démarre la GATEWAY : l'entrée unique d'une app multi-services déployée.

    `routes` : liste de (prefix, service, upstream_url, requires_auth). Le
    trafic public est authentifié à l'edge (via le `@auth_handler` chargé) puis
    forwardé en HTTP signé (svcauth) vers le bon service. Utilisé par
    `vignemale gateway` ; le secret partagé vient de VIGNEMALE_SERVICE_SECRET.
    """
    print(f"vignemale: gateway sur http://{addr} ({len(routes)} service(s))", flush=True)
    try:
        _core.serve_gateway(
            routes, addr, _auth_adapter if _auth_handler is not None else None, reuse_port
        )
    except KeyboardInterrupt:
        print("vignemale: gateway arrêtée", flush=True)


def _endpoints_to_serve() -> list:
    """Endpoints à servir par CE conteneur.

    Topologie « un conteneur par service » : le deploy pose `VIGNEMALE_SERVICE_NAME`
    → on ne sert QUE les endpoints de ce service (les autres restent joignables via
    `call()` HTTP vers leur conteneur). Sans cette variable (mono) → tout est servi.
    """
    svc = os.environ.get("VIGNEMALE_SERVICE_NAME")
    if not svc:
        return list(_endpoints)
    from .service import _services

    modules = [m for (n, m) in _services if n == svc]
    if not modules:
        # nom de service inconnu (mauvaise config) : on sert tout plutôt qu'un
        # conteneur vide, en le signalant.
        print(
            f"vignemale: VIGNEMALE_SERVICE_NAME={svc!r} ne correspond à aucun "
            "Service() déclaré — tous les endpoints sont servis.",
            flush=True,
        )
        return list(_endpoints)

    def in_service(module: str) -> bool:
        return any(module == m or module.startswith(m + ".") for m in modules)

    return [e for e in _endpoints if in_service(e[3].__module__)]  # e[3] = wrapper


def serve(addr: str = "127.0.0.1:8080", reuse_port: bool = False) -> None:
    """Démarre le serveur HTTP.

    S'arrête **gracieusement** sur Ctrl-C ou SIGTERM (containers) : healthz
    passe à 503 `shutting_down`, plus aucune connexion acceptée, les requêtes
    en vol terminent (borné par `VIGNEMALE_SHUTDOWN_TIMEOUT`, 10 s).
    """
    endpoints = _endpoints_to_serve()
    # validation de l'auth restreinte aux endpoints réellement servis (e[5] = auth)
    protected = [e[0] for e in endpoints if e[5]]
    if protected and _auth_handler is None:
        raise SystemExit(
            "vignemale: endpoint(s) protégé(s) sans @auth_handler déclaré : "
            + ", ".join(protected)
        )
    import signal as _signal

    def _sigterm(*_args):
        raise KeyboardInterrupt  # même chemin d'arrêt gracieux que Ctrl-C

    try:
        _signal.signal(_signal.SIGTERM, _sigterm)
    except ValueError:
        pass  # pas dans le thread principal (tests…) : tant pis pour SIGTERM

    svc = os.environ.get("VIGNEMALE_SERVICE_NAME")
    suffix = f" (service « {svc} »)" if svc else ""
    print(f"vignemale: {len(endpoints)} endpoint(s) sur http://{addr}{suffix}", flush=True)
    try:
        _core.serve(
            endpoints,
            addr,
            _auth_adapter if _auth_handler is not None else None,
            list(_static_routes),
            reuse_port,
        )
    except KeyboardInterrupt:
        print("vignemale: arrêté", flush=True)
