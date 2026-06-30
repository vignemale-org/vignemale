"""`vignemale deploy` : push-to-deploy vers le control plane.

Lit ~/.vignemale/credentials (posé par `vignemale login`) et pousse le dossier
courant vers le remote git du control plane, authentifié par le token. Le serveur
valide le token (better-auth), collecte la meta puis crée le déploiement.

Le serveur git peut différer de l'URL du panel : `VIGNEMALE_GIT_URL` est prioritaire,
sinon on retombe sur `cloud_url` des credentials.
"""

import os
import subprocess
import sys
import urllib.parse

from . import auth


def _git_base() -> str | None:
    creds = auth.load_token()
    if not creds:
        return None
    return os.environ.get("VIGNEMALE_GIT_URL") or creds.get("cloud_url")


def _app_name(path: str) -> str:
    """[tool.vignemale].app du pyproject, sinon le nom du dossier."""
    try:
        import tomllib

        with open(os.path.join(path, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        app = (data.get("tool", {}).get("vignemale", {}) or {}).get("app")
        if app:
            return app
    except Exception:
        pass
    return os.path.basename(os.path.abspath(path))


def _push_url(base: str, token: str, app: str) -> str:
    u = urllib.parse.urlsplit(base)
    netloc = f"vignemale:{urllib.parse.quote(token, safe='')}@{u.netloc}"
    return urllib.parse.urlunsplit((u.scheme, netloc, f"/{app}.git", "", ""))


def deploy(path: str = ".") -> int:
    creds = auth.load_token()
    if not creds:
        print("Pas connecté. Lance d'abord : vignemale login", file=sys.stderr)
        return 1
    if not os.path.isdir(os.path.join(path, ".git")):
        print("Ce dossier n'est pas un dépôt git (git init + commit requis).", file=sys.stderr)
        return 1

    base = _git_base()
    app = _app_name(path)
    token = creds["token"]
    url = _push_url(base, token, app)
    host = urllib.parse.urlsplit(base).netloc

    print(f"vignemale: déploiement de « {app} » → {host}…", flush=True)
    r = subprocess.run(
        ["git", "-C", path, "push", url, "HEAD:main"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or "").replace(token, "***").strip()
        if err:
            print(err, file=sys.stderr)
        print("vignemale: échec du push (token invalide ? lance `vignemale login`).", file=sys.stderr)
        return 1
    print(f"vignemale: ✓ poussé. Le déploiement apparaît dans le panel ({creds['cloud_url']}).")
    return 0
