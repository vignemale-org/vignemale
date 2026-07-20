"""Fullstack example: a FRONTEND served by the Rust core + the API, one single process.

The frontend (SPA — here vanilla HTML/JS, but the same principle applies to a
statically-exported Next.js with `output: 'export'` → dir="out") is served
directly by the Rust runtime: zero Python executed for the files.
`spa=True` → any route unknown to the API returns index.html (client-side routing).

    vignemale run examples/fullstack/app.py
    open http://127.0.0.1:8080
"""

from vignemale import api, serve, static_files

static_files(path="/", dir="front", spa=True)


@api(method="GET", path="/api/hello")
def hello(query) -> dict:
    return {"message": f"Hello {query.get('name', 'visitor')}!"}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
