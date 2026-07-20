"""Misconfigured app: a protected endpoint without @auth_handler → must refuse to start."""

from vignemale.api import api, serve


@api(method="GET", path="/private", auth=True)
def private():
    return {"ok": True}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
