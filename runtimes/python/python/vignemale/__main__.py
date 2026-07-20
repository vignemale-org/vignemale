"""PROD entry point: `python -m vignemale <app>` loads the app and serves.

Unlike `vignemale run` (dev tool, in vignemale-cli), this entry point only
depends on the runtime (pydantic + the Rust core) — no griffe, no
provisioning. In prod, the infrastructure already exists and the
`VIGNEMALE_*` variables are set by the deploy (provider switch): all that is
left is to load the app's modules (which register the `@api`) and serve.

This is what the Docker image produced by `vignemale build` launches, which
makes it possible NOT to ship the dev tooling (CLI, griffe, protobuf) in
production.

Usage: python -m vignemale <file|directory> [--addr host:port]
       VIGNEMALE_ADDR    listen address (default 0.0.0.0:8080)
       VIGNEMALE_WORKERS number of processes (default 1) — fork + SO_REUSEPORT
"""

import importlib
import importlib.util
import os
import sys


def _load_file(path: str) -> None:
    name = "__vig_" + os.path.splitext(os.path.basename(path))[0] + "__"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # executes the @api / Service → registers


def _is_service_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py"))


def _load_package(pkg: str, dirpath: str) -> None:
    importlib.import_module(pkg)
    for f in sorted(os.listdir(dirpath)):
        if f.endswith(".py") and not f.startswith("_"):
            importlib.import_module(f"{pkg}.{f[:-3]}")


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
                and f != "vignemale_clients"
                and _is_service_dir(full)
            ):
                _load_package(f, full)
    elif os.path.isfile(path):
        sys.path.insert(0, os.path.dirname(path))
        _load_file(path)
    else:
        raise SystemExit(f"vignemale: app not found: {path}")


def _serve_workers(path: str, addr: str, workers: int) -> None:
    """Multi-process: fork N workers sharing the port (SO_REUSEPORT).

    No provisioning here (prod): each worker loads the app after its fork and
    opens its own connections — no socket inherited across processes.
    """
    import signal
    import time

    children = []
    for _ in range(workers):
        pid = os.fork()
        if pid == 0:  # worker
            _load_app(path)
            from vignemale.api import serve

            serve(addr, reuse_port=True)
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
            time.sleep(0.1)
            _stop()
    print("vignemale: workers stopped", flush=True)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    addr = os.environ.get("VIGNEMALE_ADDR", "0.0.0.0:8080")
    path = None
    i = 0
    while i < len(argv):
        if argv[i] == "--addr" and i + 1 < len(argv):
            addr = argv[i + 1]
            i += 2
        elif path is None:
            path = argv[i]
            i += 1
        else:
            i += 1
    if path is None:
        raise SystemExit("usage: python -m vignemale <file|directory> [--addr host:port]")

    from vignemale.api import print_banner

    print_banner()

    # Gateway role ("one container per service" topology): same image, but we
    # load the app to know the paths/services, build the routes from the
    # services' URLs (discovery env) and serve the GATEWAY.
    if os.environ.get("VIGNEMALE_ROLE") == "gateway":
        _load_app(path)
        from vignemale.api import _gateway_routes, serve_gateway

        serve_gateway(_gateway_routes(), addr)
        return

    workers = int(os.environ.get("VIGNEMALE_WORKERS", "1"))
    if workers > 1:
        _serve_workers(path, addr, workers)
    else:
        _load_app(path)
        from vignemale.api import serve

        serve(addr, reuse_port=False)


if __name__ == "__main__":
    main()
