"""Exemple fullstack : un FRONT servi par le core Rust + l'API, un seul process.

Le front (SPA — ici du HTML/JS vanilla, mais c'est le même principe pour un
Next.js exporté statiquement avec `output: 'export'` → dir="out") est servi
directement par le runtime Rust : zéro Python exécuté pour les fichiers.
`spa=True` → toute route inconnue de l'API renvoie index.html (routing client).

    vignemale run examples/fullstack/app.py
    open http://127.0.0.1:8080
"""

from vignemale import api, serve, static_files

static_files(path="/", dir="front", spa=True)


@api(method="GET", path="/api/hello")
def hello(query) -> dict:
    return {"message": f"Bonjour {query.get('name', 'visiteur')} !"}


if __name__ == "__main__":
    import os

    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
