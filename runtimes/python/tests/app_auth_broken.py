"""App mal configurée : endpoint protégé sans @auth_handler → doit refuser de démarrer."""

from vignemale.api import api, serve


@api(method="GET", path="/private", auth=True)
def private():
    return {"ok": True}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
