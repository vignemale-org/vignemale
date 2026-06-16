"""`vignemale login` — authentification CLI par device-flow (OAuth 2.0 Device
Authorization Grant, RFC 8628) contre le panel Vignemale Cloud (better-auth).

Aucune dépendance : urllib + webbrowser (stdlib). Le token obtenu est stocké
dans ~/.vignemale/credentials (chmod 600) et réutilisé par les commandes qui
parlent au control plane.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import webbrowser

CLIENT_ID = "vignemale-cli"
DEFAULT_CLOUD_URL = "https://cloud.vignemale.dev"
GRANT_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


def cloud_url() -> str:
    """URL du panel Vignemale Cloud (surchargeable pour le dev/self-host)."""
    return os.environ.get("VIGNEMALE_CLOUD_URL", DEFAULT_CLOUD_URL).rstrip("/")


def credentials_path() -> str:
    base = os.environ.get("VIGNEMALE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".vignemale"
    )
    return os.path.join(base, "credentials")


def _post(url: str, data: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="POST",
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def save_token(url: str, token: str) -> str:
    path = credentials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"cloud_url": url, "token": token}, f)
    os.chmod(path, 0o600)
    return path


def load_token() -> dict | None:
    """Identifiants stockés (cloud_url + token), ou None si pas connecté."""
    try:
        with open(credentials_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def logout() -> None:
    try:
        os.remove(credentials_path())
        print("vignemale: déconnecté.")
    except FileNotFoundError:
        print("vignemale: déjà déconnecté.")


def login() -> None:
    base = cloud_url()

    # 1) demander un device code + user code
    status, d = _post(f"{base}/api/auth/device/code", {"client_id": CLIENT_ID})
    if status >= 400 or "device_code" not in d:
        raise SystemExit(f"vignemale: demande de code échouée ({status}) : {d or 'réponse vide'}")
    device_code = d["device_code"]
    user_code = d.get("user_code", "?")
    verify_uri = d.get("verification_uri") or f"{base}/device"
    verify_complete = d.get("verification_uri_complete") or verify_uri
    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 600))

    print(f"\n  Ouvre cette page pour autoriser le CLI :\n    {verify_uri}")
    print(f"  et entre le code :  {user_code}\n", flush=True)
    try:
        webbrowser.open(verify_complete)
    except Exception:
        pass

    # 2) poller le token jusqu'à approbation
    print("  En attente d'approbation dans le navigateur…", flush=True)
    while time.time() < deadline:
        time.sleep(interval)
        status, t = _post(
            f"{base}/api/auth/device/token",
            {"device_code": device_code, "client_id": CLIENT_ID, "grant_type": GRANT_DEVICE_CODE},
        )
        if status < 400 and t.get("access_token"):
            path = save_token(base, t["access_token"])
            print(f"\n  ✓ Connecté à {base}")
            print(f"  (identifiants : {path})")
            return
        err = t.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise SystemExit(f"vignemale: connexion refusée ({err or status}).")
    raise SystemExit("vignemale: délai d'approbation dépassé. Relance `vignemale login`.")
