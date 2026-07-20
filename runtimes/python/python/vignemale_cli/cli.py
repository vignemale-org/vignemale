"""Vignemale CLI.

    vignemale run app.py            # a single file
    vignemale run ./myapp           # a directory (multi-service)
    vignemale check app.py|dir      # extracts the meta graph (static, without running)

In an app directory, a service is either a **file** `myservice.py`,
or a **directory** `myservice/` (package: `__init__.py` declares the
`Service`, the endpoints live in its modules) — Encore-style.
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
    spec.loader.exec_module(mod)  # runs the @api / Service → registers them


def _load_package(pkg: str, dirpath: str) -> None:
    importlib.import_module(pkg)  # runs __init__.py (Service(...))
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
                and f != "vignemale_clients"  # generated clients ≠ service
                and _is_service_dir(full)
            ):
                _load_package(f, full)
    elif os.path.isfile(path):
        sys.path.insert(0, os.path.dirname(path))
        _load_file(path)
    else:
        raise SystemExit(f"not found: {path}")


def _provision(path: str) -> None:
    """Provisions the local infra declared by the app (statically, without running it)."""
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
    """Applies the migrations of databases declared with a `migrations` directory.
    The app must be loaded (the SQLDatabase objects are then registered)."""
    from vignemale.sqldb import _databases

    for db in _databases:
        if db._migrations:
            n = db.migrate()
            if n:
                print(f'vignemale: {n} migration(s) applied on "{db.name}"',
                      flush=True)


def _run_one(path: str, addr: str, reuse_port: bool) -> None:
    """Loads the app and serves it (one worker, or single-process mode)."""
    _load_app(path)
    from vignemale.api import serve

    serve(addr, reuse_port=reuse_port)


def _run_workers(path: str, addr: str, workers: int) -> None:
    """Multi-process: fork N workers that share the port (SO_REUSEPORT).

    The supervisor parent NEVER touches the Rust core (otherwise the tokio
    runtime started before the fork would corrupt the workers). Provisioning
    (which talks to the core) therefore runs in a throwaway fork; then each
    worker, after its own fork, reloads the app and opens its own connections —
    no socket inherited/shared between processes.
    """
    import signal
    import time

    # provision + migrations in a throwaway process → the parent stays free
    # of any tokio, and the migrations run only once (not per worker).
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
            _provision(path)  # idempotent: sets the local DSN in THIS worker
            _run_one(path, addr, reuse_port=True)
            os._exit(0)
        children.append(pid)

    print(f"vignemale: {workers} workers on http://{addr}", flush=True)
    stopping = {"v": False}

    def _stop(*_a):
        if stopping["v"]:
            return
        stopping["v"] = True
        for pid in children:
            try:
                os.kill(pid, signal.SIGINT)  # triggers the graceful drain
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # wait for all workers; if one dies without us stopping, stop everything
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
            time.sleep(0.1)  # let the signal propagate on crash
            _stop()
    print("vignemale: workers stopped", flush=True)


def cmd_run(args):
    # Infrastructure-from-Code: provision the local infra BEFORE importing the code.
    workers = int(os.environ.get("VIGNEMALE_WORKERS", "1"))
    if workers > 1:
        # provisioning happens in _run_workers (throwaway fork) to keep the
        # supervisor parent free of any Rust runtime before the forks.
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
    _load_app(args.path)  # load the app → the auth handler is available at the edge
    from vignemale.api import serve_gateway

    serve_gateway(routes, args.addr)


def cmd_gen(args):
    from .gen import generate

    for f in generate(args.path):
        print(f"vignemale: wrote {f}")


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


def cmd_gdpr(args):
    import json as _json

    from vignemale import gdpr

    if args.action == "map":
        _load_app(args.path)  # the map is pure metadata: no DB needed
        print(_json.dumps(gdpr.data_map(), indent=2, ensure_ascii=False))
        return

    if args.subject is None:
        raise SystemExit("vignemale gdpr: --subject is required for export/forget")
    _provision(args.path)
    _load_app(args.path)
    subject = int(args.subject) if args.subject.isdigit() else args.subject
    if args.action == "export":
        print(_json.dumps(gdpr.export_subject(subject), indent=2, ensure_ascii=False))
    elif args.action == "forget":
        report = gdpr.forget_subject(subject, dry_run=args.dry_run)
        print(_json.dumps(report, indent=2, ensure_ascii=False))


def cmd_login(args):
    from . import auth

    auth.login()


def cmd_logout(args):
    from . import auth

    auth.logout()


def cmd_deploy(args):
    from .deploy import deploy

    raise SystemExit(deploy(args.path))


def cmd_init(args):
    from .init import init

    raise SystemExit(init(args.name, args.path))


def cmd_link(args):
    from .link import link

    raise SystemExit(link(args.name, args.path))


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
    """Validates the sql() queries via PREPARE (sqlx mechanism, at check time)."""
    _provision(path)
    _load_app(path)
    from vignemale.datamodel import check_sql_queries

    report = check_sql_queries()
    if not report:
        print("vignemale: no sql() query declared")
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
        raise SystemExit(f"vignemale: {failed}/{total} sql() query(ies) invalid")
    print(f"vignemale: {total} sql() query(ies) validated by PREPARE")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="vignemale",
        description="Deploy your AI agents to production, from Python.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser(
        "init", help="scaffold a starter project (app.py + pyproject + .gitignore)"
    )
    p_init.add_argument("name", nargs="?", default=".", help="project name (default: current directory)")
    p_init.add_argument("path", nargs="?", default=".", help="where to create the project (default: .)")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="run the app locally (discovers the @api and serves)")
    p_run.add_argument("path", help="app file or directory")
    p_run.add_argument("--addr", default="127.0.0.1:8080", help="listen address")
    p_run.set_defaults(func=cmd_run)

    p_check = sub.add_parser(
        "check", help="extract the meta graph statically (without running the app)"
    )
    p_check.add_argument("path", help="app file or directory")
    p_check.add_argument("--raw", action="store_true", help="intermediate dict instead of the meta.proto")
    p_check.add_argument(
        "--sql",
        action="store_true",
        help="validate the sql() queries via Postgres PREPARE (without running them)",
    )
    p_check.set_defaults(func=cmd_check)

    p_gw = sub.add_parser(
        "gateway", help="single entry point for multi-services: routing + auth at the edge"
    )
    p_gw.add_argument("path", help="multi-service app directory")
    p_gw.add_argument("--addr", default="127.0.0.1:8080", help="listen address")
    p_gw.set_defaults(func=cmd_gateway)

    p_gen = sub.add_parser(
        "gen", help="generate the typed service clients (vignemale_clients/)"
    )
    p_gen.add_argument("path", help="app file or directory")
    p_gen.set_defaults(func=cmd_gen)

    p_build = sub.add_parser(
        "build", help="build the app's Docker image (generated Dockerfile)"
    )
    p_build.add_argument("path", help="app file or directory")
    p_build.add_argument("--tag", help="image tag (default: vignemale-<app>:latest)")
    p_build.add_argument(
        "--base",
        help="runtime base image (default: ghcr.io/vignemale-org/vignemale-python:latest "
        "or $VIGNEMALE_BASE_IMAGE)",
    )
    p_build.add_argument(
        "--from-source",
        dest="from_source",
        action="store_true",
        help="compile the Rust runtime inside the image instead of starting from the base image",
    )
    p_build.add_argument(
        "--platform",
        help="target platform (e.g. linux/amd64 for Scaleway from an arm64 Mac)",
    )
    p_build.add_argument(
        "--push", action="store_true", help="push the image to the registry (buildx) after build"
    )
    p_build.add_argument(
        "--print", action="store_true", help="print the Dockerfile without building"
    )
    p_build.set_defaults(func=cmd_build)

    p_gdpr = sub.add_parser(
        "gdpr", help="personal data: map · export · forget"
    )
    p_gdpr.add_argument("action", choices=["map", "export", "forget"])
    p_gdpr.add_argument("path", help="app file or directory")
    p_gdpr.add_argument("--subject", help="the person's identifier (export/forget)")
    p_gdpr.add_argument(
        "--dry-run", action="store_true", help="forget: show without erasing"
    )
    p_gdpr.set_defaults(func=cmd_gdpr)

    p_login = sub.add_parser(
        "login", help="authenticate against Vignemale Cloud (browser device-flow)"
    )
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help="remove the stored credentials")
    p_logout.set_defaults(func=cmd_logout)

    p_deploy = sub.add_parser(
        "deploy", help="push-to-deploy: push the app to the control plane (token auth)"
    )
    p_deploy.add_argument("path", nargs="?", default=".", help="app directory (default: .)")
    p_deploy.set_defaults(func=cmd_deploy)

    p_link = sub.add_parser(
        "link", help="link this repo to a project created in the panel (writes pyproject)"
    )
    p_link.add_argument("name", help="project name (as created in the panel)")
    p_link.add_argument("path", nargs="?", default=".", help="app directory (default: .)")
    p_link.set_defaults(func=cmd_link)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
