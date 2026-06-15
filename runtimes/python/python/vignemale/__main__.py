"""Point d'entrée PROD : `python -m vignemale <app>` charge l'app et sert.

À la différence de `vignemale run` (outil dev, dans vignemale-cli), ce point
d'entrée ne dépend que du runtime (pydantic + le cœur Rust) — pas de griffe ni
de provisioning. En prod, l'infrastructure est déjà créée et les variables
`VIGNEMALE_*` sont posées par le deploy (provider switch) : il n'y a qu'à
charger les modules de l'app (qui enregistrent les `@api`) et servir.

C'est ce que lance l'image Docker produite par `vignemale build`, ce qui permet
de NE PAS embarquer l'outillage dev (CLI, griffe, protobuf) en production.

Usage : python -m vignemale <fichier|dossier> [--addr host:port]
        VIGNEMALE_ADDR    adresse d'écoute (défaut 0.0.0.0:8080)
        VIGNEMALE_WORKERS nombre de process (défaut 1) — fork + SO_REUSEPORT
"""

import importlib
import importlib.util
import os
import sys


def _load_file(path: str) -> None:
    name = "__vig_" + os.path.splitext(os.path.basename(path))[0] + "__"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # exécute les @api / Service → enregistre


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
        raise SystemExit(f"vignemale: app introuvable : {path}")


def _serve_workers(path: str, addr: str, workers: int) -> None:
    """Multi-process : fork N workers partageant le port (SO_REUSEPORT).

    Pas de provisioning ici (prod) : chaque worker charge l'app après son fork
    et ouvre ses propres connexions — aucune socket héritée entre process.
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
    print("vignemale: workers arrêtés", flush=True)


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
        raise SystemExit("usage: python -m vignemale <fichier|dossier> [--addr host:port]")

    workers = int(os.environ.get("VIGNEMALE_WORKERS", "1"))
    if workers > 1:
        _serve_workers(path, addr, workers)
    else:
        _load_app(path)
        from vignemale.api import serve

        serve(addr, reuse_port=False)


if __name__ == "__main__":
    main()
