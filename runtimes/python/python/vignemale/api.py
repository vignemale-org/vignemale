"""SDK API de Vignemale : le décorateur `@api` (typé Pydantic) + `serve()`.

    from pydantic import BaseModel
    from vignemale.api import api, serve

    class ChatRequest(BaseModel):
        prompt: str

    @api(method="POST", path="/chat")
    def chat(body: ChatRequest) -> ChatReply:    # validé au runtime + extrait en statique
        ...

    serve("127.0.0.1:8080")
"""

import functools
import json
from typing import Callable, get_type_hints

from . import _core

# Registre des endpoints déclarés (rempli par le décorateur à l'import de l'app).
_endpoints: list = []


class HTTPError(Exception):
    """Erreur HTTP renvoyée par un handler (statut + détail).

    Reconnue par le runtime → renvoie ce statut au lieu d'un 500 :

        raise HTTPError(404, "introuvable")
    """

    def __init__(self, status: int, detail=None):
        self.vignemale_status = int(status)
        self.vignemale_body = json.dumps(
            {"detail": detail} if detail is not None else {"error": f"HTTP {status}"}
        )
        super().__init__(f"HTTP {status}")


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
      **validée** (et coercée) au runtime avant l'appel du handler.
    - Si le retour est un modèle Pydantic, il est sérialisé automatiquement.
    - `stream=True` : le handler reçoit `stream` et pousse des fragments (SSE).
    """

    def decorator(func: Callable) -> Callable:
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        body_model = _pydantic_model(hints.get("body"))

        @functools.wraps(func)
        def wrapper(**kwargs):
            if body_model is not None and "body" in kwargs:
                from pydantic import ValidationError

                try:
                    kwargs["body"] = body_model.model_validate(kwargs["body"])
                except ValidationError as e:
                    raise HTTPError(422, json.loads(e.json())) from None
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
