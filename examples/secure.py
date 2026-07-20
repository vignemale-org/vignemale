"""Example: API protected by an auth handler (Encore-style).

ONE `@auth_handler` per app: it receives the token (header `Authorization:
Bearer …`, or `?token=` for header-less clients like EventSource) and
returns the auth data — or `None` (→ 401). Authentication is enforced
by the Rust CORE before the handler: a protected stream returns a real 401
before opening the flow.

    VIGNEMALE_API_TOKEN=my-secret vignemale run examples/secure.py

    curl 127.0.0.1:8080/public
    curl 127.0.0.1:8080/me                                    # → 401
    curl -H "Authorization: Bearer my-secret" 127.0.0.1:8080/me
    curl -N "127.0.0.1:8080/chat?token=my-secret" -X POST -d '{"prompt":"hi"}'
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
    prompt = (body or {}).get("prompt", "hi")
    for mot in f"Hello {auth['user_id']}, you said: {prompt}".split(" "):
        stream.write(mot + " ")
        time.sleep(0.05)


if __name__ == "__main__":
    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
