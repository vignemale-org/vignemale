"""Mini-app de démo : on écrit des @api, on lance `serve`, on curl.

    python tests/app_hello.py        # sert sur 127.0.0.1:8077
"""

from vignemale.api import api, serve


@api(method="GET", path="/hello")
def hello():
    return {"msg": "bonjour depuis vignemale"}


@api(method="GET", path="/greet/:name")
def greet(name):
    return {"hello": name}


@api(method="POST", path="/echo")
def echo(body):
    return {"you_sent": body}


@api(method="GET", path="/boom")
def boom():
    raise ValueError("explosion contrôlée")


@api(method="GET", path="/stream", stream=True)
def stream_demo(stream):
    # Simule un agent qui streame ses tokens (un vrai modèle ferait pareil).
    import time

    for word in "ceci est un flux vignemale token par token".split(" "):
        stream.write(word + " ")
        time.sleep(0.05)


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8077"))
