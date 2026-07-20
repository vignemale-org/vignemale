"""Demo mini-app: write some @api handlers, run `serve`, then curl.

    python tests/app_hello.py        # serves on 127.0.0.1:8077
"""

from vignemale.api import api, serve


@api(method="GET", path="/hello")
def hello():
    return {"msg": "hello from vignemale"}


@api(method="GET", path="/greet/:name")
def greet(name):
    return {"hello": name}


@api(method="POST", path="/echo")
def echo(body):
    return {"you_sent": body}


@api(method="GET", path="/search")
def search(query):
    return {"q": query.get("q"), "limit": query.get("limit", "10")}


@api(method="GET", path="/whoami")
def whoami(headers):
    return {"client": headers.get("x-client", "unknown")}


@api(method="GET", path="/boom")
def boom():
    raise ValueError("controlled explosion")


@api(method="GET", path="/slow", timeout=0.5)
def slow():
    import time

    time.sleep(3)
    return {"done": True}


@api(method="GET", path="/work")
def work():
    import time

    time.sleep(1.5)  # "long" request to test the drain
    return {"done": True}


@api(method="POST", path="/small", body_limit=1024)
def small(body=None):
    return {"ok": True}


@api(method="GET", path="/stream", stream=True)
def stream_demo(stream):
    # Simulates an agent streaming its tokens (a real model would do the same).
    import time

    for word in "this is a vignemale stream token by token".split(" "):
        stream.write(word + " ")
        time.sleep(0.05)


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8077"))
