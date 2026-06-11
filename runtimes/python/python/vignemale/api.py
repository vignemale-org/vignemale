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

import functools
import inspect
import json
from typing import Callable, get_type_hints

from . import _core

# Registre des endpoints déclarés (rempli par le décorateur à l'import de l'app).
_endpoints: list = []

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


def api(*, method: str, path: str, stream: bool = False) -> Callable:
    """Déclare une fonction comme endpoint HTTP.

    - Si le paramètre `body` est annoté avec un modèle Pydantic, la requête est
      **validée** (et coercée) avant l'appel du handler (sinon → 400
      `invalid_argument` avec le détail Pydantic).
    - Si le retour est un modèle Pydantic, il est sérialisé automatiquement.
    - `stream=True` : le handler reçoit `stream` et pousse des fragments (SSE).
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
            # le runtime fournit tout (params, query, headers, body) ;
            # on ne transmet que ce que la signature du handler déclare.
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
            result = func(**kwargs)
            if _pydantic_model(type(result)) is not None:
                result = result.model_dump()
            return result

        _endpoints.append((func.__name__, method.upper(), path, wrapper, stream))
        return func  # on renvoie la fonction typée d'origine (pour pyright)

    return decorator


def serve(addr: str = "127.0.0.1:8080") -> None:
    """Démarre le serveur HTTP (bloque jusqu'à Ctrl-C)."""
    print(f"vignemale: {len(_endpoints)} endpoint(s) sur http://{addr}", flush=True)
    try:
        _core.serve(list(_endpoints), addr)
    except KeyboardInterrupt:
        print("vignemale: arrêté", flush=True)
