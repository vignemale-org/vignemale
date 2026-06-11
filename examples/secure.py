"""Exemple : API protégée par un auth handler (façon Encore).

UN `@auth_handler` par app : il reçoit le token (header `Authorization:
Bearer …`, ou `?token=` pour les clients sans en-têtes type EventSource) et
renvoie les données d'auth — ou `None` (→ 401). L'authentification est jouée
par le CORE Rust avant le handler : un stream protégé renvoie un vrai 401
avant d'ouvrir le flux.

    VIGNEMALE_API_TOKEN=mon-secret vignemale run examples/secure.py

    curl 127.0.0.1:8080/public
    curl 127.0.0.1:8080/me                                    # → 401
    curl -H "Authorization: Bearer mon-secret" 127.0.0.1:8080/me
    curl -N "127.0.0.1:8080/chat?token=mon-secret" -X POST -d '{"prompt":"salut"}'
"""

import os
import time

from vignemale import api, auth_handler, serve


@auth_handler
def check_token(token):
    expected = os.environ.get("VIGNEMALE_API_TOKEN", "dev-token")
    if token == expected:
        return {"user_id": "jacques", "plan": "pro"}
    return None  # → 401 unauthenticated


@api(method="GET", path="/public")
def public():
    return {"open": True}


@api(method="GET", path="/me", auth=True)
def me(auth):
    return {"user": auth["user_id"], "plan": auth["plan"]}


@api(method="POST", path="/chat", auth=True, stream=True)
def chat(stream, auth, body=None):
    prompt = (body or {}).get("prompt", "salut")
    for mot in f"Bonjour {auth['user_id']}, tu as dit : {prompt}".split(" "):
        stream.write(mot + " ")
        time.sleep(0.05)


if __name__ == "__main__":
    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
