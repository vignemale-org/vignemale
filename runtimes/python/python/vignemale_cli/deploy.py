"""`vignemale deploy`: push-to-deploy to the control plane.

Reads ~/.vignemale/credentials (set by `vignemale login`) and pushes the current
directory to the control plane's git remote, authenticated by the token. The server
validates the token (better-auth), collects the meta then creates the deployment.

The git server may differ from the panel URL: `VIGNEMALE_GIT_URL` takes priority,
otherwise we fall back to the credentials' `cloud_url`.
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
    """[tool.vignemale].app from the pyproject, otherwise the directory name."""
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
        print("Not logged in. First run: vignemale login", file=sys.stderr)
        return 1
    if not os.path.isdir(os.path.join(path, ".git")):
        print("This directory is not a git repo (git init + commit required).", file=sys.stderr)
        return 1

    base = _git_base()
    app = _app_name(path)
    token = creds["token"]
    url = _push_url(base, token, app)
    host = urllib.parse.urlsplit(base).netloc

    print(f'vignemale: deploying "{app}" → {host}…', flush=True)
    r = subprocess.run(
        ["git", "-C", path, "push", url, "HEAD:main"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or "").replace(token, "***").strip()
        if err:
            print(err, file=sys.stderr)
        print("vignemale: push failed (invalid token? run `vignemale login`).", file=sys.stderr)
        return 1
    print(f"vignemale: ✓ pushed. The deployment appears in the panel ({creds['cloud_url']}).")
    return 0
