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
            elif (
                not f.startswith(("_", "."))
                and f != "vignemale_clients"  # clients générés ≠ service
                and _is_service_dir(full)
            ):
                _load_package(f, full)
    elif os.path.isfile(path):
        sys.path.insert(0, os.path.dirname(path))
        _load_file(path)
    else:
        raise SystemExit(f"introuvable: {path}")


def _provision(path: str) -> None:
    """Provisionne l'infra locale déclarée par l'app (statiquement, sans l'exécuter)."""
    from .collect import extract_path

    extracted, _ = extract_path(path)
    databases = extracted.get("databases") or []
    buckets = extracted.get("buckets") or []
    if databases or buckets:
        from . import devinfra

        if databases:
            devinfra.provision_local(databases)
        if buckets:
            devinfra.provision_buckets(buckets)


def _migrate(path: str) -> None:
    """Applique les migrations des bases déclarées avec un dossier `migrations`.
    L'app doit être chargée (les SQLDatabase sont alors enregistrées)."""
    from vignemale.sqldb import _databases

    for db in _databases:
        if db._migrations:
            n = db.migrate()
            if n:
                print(f"vignemale: {n} migration(s) appliquée(s) sur « {db.name} »",
                      flush=True)


def _run_one(path: str, addr: str, reuse_port: bool) -> None:
    """Charge l'app et sert (un worker, ou le mode mono-process)."""
    _load_app(path)
    from vignemale.api import serve

    serve(addr, reuse_port=reuse_port)


def _run_workers(path: str, addr: str, workers: int) -> None:
    """Multi-process : fork N workers qui partagent le port (SO_REUSEPORT).

    Le parent superviseur ne touche JAMAIS le core Rust (sinon le runtime
    tokio démarré avant le fork corromprait les workers). La provision (qui
    parle au core) tourne donc dans un fork jetable ; puis chaque worker, après
    son propre fork, recharge l'app et ouvre ses propres connexions — aucune
    socket héritée/partagée entre process.
    """
    import signal
    import time

    # provision + migrations dans un process jetable → le parent reste vierge
    # de tout tokio, et les migrations ne tournent qu'une fois (pas par worker).
    pid = os.fork()
    if pid == 0:
        _provision(path)
        _load_app(path)
        _migrate(path)
        os._exit(0)
    os.waitpid(pid, 0)

    children = []
    for _ in range(workers):
        pid = os.fork()
        if pid == 0:  # worker
            _provision(path)  # idempotent : pose le DSN local dans CE worker
            _run_one(path, addr, reuse_port=True)
            os._exit(0)
        children.append(pid)

    print(f"vignemale: {workers} workers sur http://{addr}", flush=True)
    stopping = {"v": False}

    def _stop(*_a):
        if stopping["v"]:
            return
        stopping["v"] = True
        for pid in children:
            try:
                os.kill(pid, signal.SIGINT)  # déclenche le drain gracieux
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # attend tous les workers ; si l'un meurt sans qu'on arrête, on stoppe tout
    alive = set(children)
    while alive:
        try:
            pid, _ = os.wait()
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        alive.discard(pid)
        if not stopping["v"] and alive:
            time.sleep(0.1)  # laisser le signal se propager si crash
            _stop()
    print("vignemale: workers arrêtés", flush=True)


def cmd_run(args):
    # Infrastructure-from-Code : provisionne le local AVANT d'importer le code.
    workers = int(os.environ.get("VIGNEMALE_WORKERS", "1"))
    if workers > 1:
        # la provision se fait dans _run_workers (fork jetable) pour garder le
        # parent superviseur vierge de tout runtime Rust avant les forks.
        _run_workers(args.path, args.addr, workers)
    else:
        _provision(args.path)
        _load_app(args.path)
        _migrate(args.path)
        from vignemale.api import serve

        serve(args.addr, reuse_port=False)


def cmd_gateway(args):
    from .gateway import build_routes

    routes = build_routes(args.path)
    _load_app(args.path)  # charge l'app → l'auth handler est dispo à l'edge
    from vignemale.api import serve_gateway

    serve_gateway(routes, args.addr)


def cmd_gen(args):
    from .gen import generate

    for f in generate(args.path):
        print(f"vignemale: écrit {f}")


def cmd_deploy(args):
    import os

    from .collect import extract_path
    from vignemale_deploy import Target, build_plan, render

    extracted, app_name = extract_path(args.path)
    target = Target(
        app=app_name,
        env=args.env,
        region=args.region,
        image=args.image,
        db_backend=args.db,
        scw_access_key=os.environ.get("SCW_ACCESS_KEY"),
        scw_secret_key=os.environ.get("SCW_SECRET_KEY"),
        scw_project_id=os.environ.get("SCW_DEFAULT_PROJECT_ID")
        or os.environ.get("SCW_PROJECT_ID"),
    )

    if args.dry_run:
        # hors-ligne : provider=None → le plan suppose un compte vierge.
        print(render(build_plan(extracted, target)))
        return

    # apply réel : credentials Scaleway requis (compte DU CLIENT).
    creds = target.scw_access_key and target.scw_secret_key and target.scw_project_id
    if not creds:
        raise SystemExit(
            "vignemale deploy: pose SCW_ACCESS_KEY / SCW_SECRET_KEY / "
            "SCW_DEFAULT_PROJECT_ID (compte Scaleway cible), ou utilise --dry-run."
        )
    if not args.image:
        raise SystemExit(
            "vignemale deploy: --image requis (ref poussée au Container Registry). "
            "`vignemale build` puis pousse l'image, ou --dry-run pour voir le plan."
        )

    from vignemale_deploy import ScalewayProvider, apply_plan

    provider = ScalewayProvider(target)
    # montre le plan réel (lookup) puis demande confirmation — ressources facturables.
    plan = build_plan(extracted, target, provider)
    print(render(plan))
    if not args.yes:
        ans = input("\nAppliquer ce plan dans le compte Scaleway ? [oui/N] ").strip().lower()
        if ans not in ("oui", "o", "yes", "y"):
            raise SystemExit("vignemale deploy: annulé.")

    # valeurs de secrets : depuis l'env VIGNEMALE_SECRET_<NOM> au moment du deploy.
    secret_values = {}
    for name in extracted.get("secrets") or []:
        env_name = f"VIGNEMALE_SECRET_{name.upper().replace('-', '_')}"
        if env_name in os.environ:
            secret_values[name] = os.environ[env_name]

    dep = apply_plan(extracted, target, provider, secret_values, on_progress=lambda m: print("  " + m))
    print(f"\nvignemale: « {dep.app} » déployé sur {dep.env}.")
    if dep.url:
        print(f"  URL : {dep.url}")


def cmd_build(args):
    from .build import build

    build(
        args.path,
        tag=args.tag,
        print_only=args.print,
        from_source=args.from_source,
        base=args.base,
        platform=args.platform,
        push=args.push,
    )


def cmd_rgpd(args):
    import json as _json

    from vignemale import rgpd

    if args.action == "map":
        _load_app(args.path)  # la carte est pure métadonnée : pas besoin de DB
        print(_json.dumps(rgpd.data_map(), indent=2, ensure_ascii=False))
        return

    if args.subject is None:
        raise SystemExit("vignemale rgpd: --subject est requis pour export/forget")
    _provision(args.path)
    _load_app(args.path)
    subject = int(args.subject) if args.subject.isdigit() else args.subject
    if args.action == "export":
        print(_json.dumps(rgpd.export_subject(subject), indent=2, ensure_ascii=False))
    elif args.action == "forget":
        report = rgpd.forget_subject(subject, dry_run=args.dry_run)
        print(_json.dumps(report, indent=2, ensure_ascii=False))


def cmd_check(args):
    if args.sql:
        return _check_sql(args.path)

    from google.protobuf import json_format

    from .collect import build_meta, extract_path

    extracted, app_name = extract_path(args.path)
    if args.raw:
        import json

        print(json.dumps(extracted, indent=2, ensure_ascii=False))
    else:
        print(json_format.MessageToJson(build_meta(extracted, app_name), indent=2))


def _check_sql(path: str):
    """Valide les requêtes sql() par PREPARE (mécanisme sqlx, au moment check)."""
    _provision(path)
    _load_app(path)
    from vignemale.datamodel import check_sql_queries

    report = check_sql_queries()
    if not report:
        print("vignemale: aucune requête sql() déclarée")
        return
    failed = 0
    for r in report:
        if r["ok"]:
            params = ", ".join(r.get("params") or []) or "—"
            cols = ", ".join(
                f"{c['name']} {c['type']}" for c in (r.get("columns") or [])
            )
            print(f"  ✓ {r['query']}  ({params}) → {cols}")
        else:
            failed += 1
            print(f"  ✗ {r['query']}  {r['error']}")
    total = len(report)
    if failed:
        raise SystemExit(f"vignemale: {failed}/{total} requête(s) sql() invalide(s)")
    print(f"vignemale: {total} requête(s) sql() validée(s) par PREPARE")


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
    p_check.add_argument(
        "--sql",
        action="store_true",
        help="valide les requêtes sql() par PREPARE Postgres (sans les exécuter)",
    )
    p_check.set_defaults(func=cmd_check)

    p_gw = sub.add_parser(
        "gateway", help="entrée unique multi-services : route + auth à l'edge"
    )
    p_gw.add_argument("path", help="dossier de l'app multi-services")
    p_gw.add_argument("--addr", default="127.0.0.1:8080", help="adresse d'écoute")
    p_gw.set_defaults(func=cmd_gateway)

    p_gen = sub.add_parser(
        "gen", help="génère les clients de services typés (vignemale_clients/)"
    )
    p_gen.add_argument("path", help="fichier ou dossier de l'app")
    p_gen.set_defaults(func=cmd_gen)

    p_build = sub.add_parser(
        "build", help="construit l'image Docker de l'app (Dockerfile généré)"
    )
    p_build.add_argument("path", help="fichier ou dossier de l'app")
    p_build.add_argument("--tag", help="tag de l'image (défaut: vignemale-<app>:latest)")
    p_build.add_argument(
        "--base",
        help="image de base runtime (défaut: ghcr.io/jacqkues/vignemale-python:latest "
        "ou $VIGNEMALE_BASE_IMAGE)",
    )
    p_build.add_argument(
        "--from-source",
        dest="from_source",
        action="store_true",
        help="compile le runtime Rust dans l'image au lieu de partir de l'image de base",
    )
    p_build.add_argument(
        "--platform",
        help="plateforme cible (ex. linux/amd64 pour Scaleway depuis un Mac arm64)",
    )
    p_build.add_argument(
        "--push", action="store_true", help="pousse l'image au registry (buildx) après build"
    )
    p_build.add_argument(
        "--print", action="store_true", help="affiche le Dockerfile sans builder"
    )
    p_build.set_defaults(func=cmd_build)

    p_deploy = sub.add_parser(
        "deploy", help="déploie l'app sur Scaleway (control plane) — pour l'instant --dry-run"
    )
    p_deploy.add_argument("path", help="fichier ou dossier de l'app")
    p_deploy.add_argument("--env", default="prod", help="environnement (défaut: prod)")
    p_deploy.add_argument("--region", default="fr-par", help="région Scaleway (défaut: fr-par)")
    p_deploy.add_argument("--image", help="ref/digest de l'image d'app (vignemale build)")
    p_deploy.add_argument(
        "--db", choices=["serverless", "managed"], default="serverless",
        help="backend base de données : serverless (scale-to-zero, défaut) ou managed",
    )
    p_deploy.add_argument(
        "--dry-run", action="store_true", help="montre le plan de déploiement sans rien créer"
    )
    p_deploy.add_argument(
        "--yes", action="store_true", help="applique sans demander confirmation"
    )
    p_deploy.set_defaults(func=cmd_deploy)

    p_rgpd = sub.add_parser(
        "rgpd", help="données personnelles : map (carte) · export · forget"
    )
    p_rgpd.add_argument("action", choices=["map", "export", "forget"])
    p_rgpd.add_argument("path", help="fichier ou dossier de l'app")
    p_rgpd.add_argument("--subject", help="identifiant de la personne (export/forget)")
    p_rgpd.add_argument(
        "--dry-run", action="store_true", help="forget : montre sans effacer"
    )
    p_rgpd.set_defaults(func=cmd_rgpd)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
