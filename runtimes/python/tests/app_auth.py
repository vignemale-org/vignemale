"""Mini-app pour les tests d'authentification."""

from vignemale.api import api, auth_handler, serve


@auth_handler
def check_token(token):
    if token == "sesame":
        return {"user_id": "u-42", "role": "admin"}
    return None


@api(method="GET", path="/public")
def public():
    return {"open": True}


@api(method="GET", path="/private", auth=True)
def private(auth):
    return {"user": auth["user_id"], "role": auth["role"]}


@api(method="GET", path="/private-opaque", auth=True)
def private_opaque():  # ne déclare pas `auth` : protégé quand même
    return {"ok": True}


@api(method="GET", path="/private-stream", auth=True, stream=True)
def private_stream(stream, auth):
    stream.write(f"bienvenue {auth['user_id']}")


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
