"""`vignemale link <name>` : rattache le dépôt local à un projet du panel.

Le projet est créé côté Vignemale Cloud (panel « New project ») ; `link` écrit
simplement `[tool.vignemale].app = "<name>"` dans le pyproject.toml local, de
sorte que le prochain `vignemale deploy` pousse vers ce projet (le remote git est
`/<app>.git`, voir deploy.py). Aucune dépendance réseau : c'est de la config locale.

Pas de writer TOML en stdlib → édition texte prudente : on met à jour la clé si
elle existe, on l'insère dans `[tool.vignemale]` si la section existe, sinon on
crée la section (ou le fichier).
"""

import os
import re
import sys


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    return s[:48]


def _apply(text: str, app: str) -> str:
    line = f'app = "{app}"'
    lines = text.splitlines()

    # Repère la section [tool.vignemale] (et ses bornes).
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "[tool.vignemale]":
            start = i
            break

    if start is None:
        prefix = text if text.endswith("\n") or text == "" else text + "\n"
        sep = "\n" if prefix and not prefix.endswith("\n\n") and prefix != "" else ""
        return f"{prefix}{sep}[tool.vignemale]\n{line}\n"

    # Fin de section = prochaine ligne qui ouvre une table [..], sinon fin.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"\s*\[", lines[j]):
            end = j
            break

    for k in range(start + 1, end):
        if re.match(r"\s*app\s*=", lines[k]):
            lines[k] = line
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    lines.insert(start + 1, line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def link(name: str, path: str = ".") -> int:
    app = _slug(name)
    if not app:
        print("Nom de projet invalide (lettres, chiffres, tirets).", file=sys.stderr)
        return 1

    pyproject = os.path.join(path, "pyproject.toml")
    text = ""
    if os.path.isfile(pyproject):
        with open(pyproject, encoding="utf-8") as f:
            text = f.read()

    with open(pyproject, "w", encoding="utf-8") as f:
        f.write(_apply(text, app))

    print(f"vignemale: ✓ dépôt rattaché au projet « {app} ».")
    print("vignemale: déploie avec  vignemale deploy")
    return 0
