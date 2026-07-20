"""`vignemale link <name>`: links the local repo to a project in the panel.

The project is created on Vignemale Cloud (panel "New project"); `link` simply
writes `[tool.vignemale].app = "<name>"` into the local pyproject.toml, so
that the next `vignemale deploy` pushes to that project (the git remote is
`/<app>.git`, see deploy.py). No network dependency: it's local config.

No TOML writer in the stdlib → careful text editing: we update the key if
it exists, insert it into `[tool.vignemale]` if the section exists, otherwise
create the section (or the file).
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

    # Locate the [tool.vignemale] section (and its bounds).
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "[tool.vignemale]":
            start = i
            break

    if start is None:
        prefix = text if text.endswith("\n") or text == "" else text + "\n"
        sep = "\n" if prefix and not prefix.endswith("\n\n") and prefix != "" else ""
        return f"{prefix}{sep}[tool.vignemale]\n{line}\n"

    # End of section = next line that opens a table [..], otherwise the end.
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
        print("Invalid project name (letters, digits, dashes).", file=sys.stderr)
        return 1

    pyproject = os.path.join(path, "pyproject.toml")
    text = ""
    if os.path.isfile(pyproject):
        with open(pyproject, encoding="utf-8") as f:
            text = f.read()

    with open(pyproject, "w", encoding="utf-8") as f:
        f.write(_apply(text, app))

    print(f'vignemale: ✓ repo linked to project "{app}".')
    print("vignemale: deploy with  vignemale deploy")
    return 0
