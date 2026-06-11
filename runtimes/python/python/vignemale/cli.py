"""CLI Vignemale.

    vignemale run app.py            # un fichier
    vignemale run ./monapp          # un dossier (multi-service)
    vignemale check app.py|dossier  # extrait le graphe meta (statique, sans exécuter)

Dans un dossier d'app, un service est soit un **fichier** `monservice.py`,
soit un **dossier** `monservice/` (package : `__init__.py` déclare le
`Service`, les endpoints vivent dans ses modules) — façon Encore.
"""

import argparse
import importlib
import importlib.util
import os
import sys


def _load_file(path: str) -> None:
    name = "__vig_" + os.path.splitext(os.path.basename(path))[0] + "__"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # exécute les @api / Service → enregistre


def _load_package(pkg: str, dirpath: str) -> None:
    importlib.import_module(pkg)  # exécute __init__.py (Service(...))
    for f in sorted(os.listdir(dirpath)):
        if f.endswith(".py") and not f.startswith("_"):
            importlib.import_module(f"{pkg}.{f[:-3]}")


def _is_service_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py"))


def _load_app(path: str) -> None:
    path = os.path.abspath(path)
    if os.path.isdir(path):
        sys.path.insert(0, path)
        for f in sorted(os.listdir(path)):
            full = os.path.join(path, f)
            if f.endswith(".py") and not f.startswith("_"):
                _load_file(full)
            elif not f.startswith(("_", ".")) and _is_service_dir(full):
                _load_package(f, full)
    elif os.path.isfile(path):
        sys.path.insert(0, os.path.dirname(path))
        _load_file(path)
    else:
        raise SystemExit(f"introuvable: {path}")


def cmd_run(args):
    # Infrastructure-from-Code : on lit les ressources déclarées (statiquement,
    # sans exécuter l'app) et on provisionne le local AVANT d'importer le code.
    from vignemale.collect import extract_path

    extracted, _ = extract_path(args.path)
    databases = extracted.get("databases") or []
    if databases:
        from vignemale import devinfra

        devinfra.provision_local(databases)

    _load_app(args.path)
    from vignemale.api import serve

    serve(args.addr)


def cmd_check(args):
    from google.protobuf import json_format

    from vignemale.collect import build_meta, extract_path

    extracted, app_name = extract_path(args.path)
    if args.raw:
        import json

        print(json.dumps(extracted, indent=2, ensure_ascii=False))
    else:
        print(json_format.MessageToJson(build_meta(extracted, app_name), indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="vignemale",
        description="Déploie tes agents IA en production, depuis Python.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="lance l'app en local (découvre les @api et sert)")
    p_run.add_argument("path", help="fichier ou dossier de l'app")
    p_run.add_argument("--addr", default="127.0.0.1:8080", help="adresse d'écoute")
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser(
        "check", help="extrait le graphe meta statiquement (sans exécuter l'app)"
    )
    p_check.add_argument("path", help="fichier ou dossier de l'app")
    p_check.add_argument("--raw", action="store_true", help="dict intermédiaire au lieu du meta.proto")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
