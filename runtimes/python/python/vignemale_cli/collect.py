"""Extracteur STATIQUE du graphe `meta` — via griffe, **sans exécuter l'app**.

Parse le source (décorateurs `@api`, `Service(...)`, annotations, modèles Pydantic)
et construit le **vrai `meta.proto`** (`Data` : services · rpcs · decls), émis en
protojson — le graphe canonique, diffable dans une PR.

    python -m vignemale_cli.collect ../../examples/typed.py       # un fichier
    python -m vignemale_cli.collect ../../examples/shop           # un dossier (multi-service)
    python -m vignemale_cli.collect ... --raw                     # dict intermédiaire
"""

import ast
import json
import os
import sys

import griffe
from google.protobuf import json_format

from vignemale_cli.parser.meta.v1 import meta_pb2 as meta
from vignemale_cli.parser.schema.v1 import schema_pb2 as schema


# ----- 1) extraction statique (griffe) -----

def _lit(expr):
    if expr is None:
        return None
    try:
        return ast.literal_eval(str(expr))
    except Exception:
        return str(expr)


def _is_pydantic(cls) -> bool:
    return any("BaseModel" in str(b) for b in cls.bases)


def _extract_module(mod) -> tuple:
    """Renvoie (service, endpoints, models, databases, auth_handler,
    model_modules, buckets, secrets) — récursif : un package agrège ses
    sous-modules."""
    service = None
    models = {}
    model_modules = {}
    endpoints = []
    databases = []
    buckets = []
    secrets = []
    auth_fn = None

    for name, m in mod.members.items():
        kind = m.kind.value

        if kind == "module":  # sous-module d'un dossier-service
            svc2, eps2, mods2, dbs2, auth2, modmods2, bk2, sec2 = _extract_module(m)
            service = service or svc2
            endpoints.extend(eps2)
            models.update(mods2)
            model_modules.update(modmods2)
            databases.extend(db for db in dbs2 if db not in databases)
            buckets.extend(b for b in bk2 if b not in buckets)
            secrets.extend(s for s in sec2 if s not in secrets)
            auth_fn = auth_fn or auth2
            continue

        if kind == "class" and _is_pydantic(m):
            fields = {}
            for attr_name, attr in m.members.items():
                if attr.kind.value == "attribute" and not attr_name.startswith("_"):
                    fields[attr_name] = {
                        "type": str(attr.annotation) if attr.annotation is not None else None,
                        "required": attr.value is None,
                        "default": _lit(attr.value),
                    }
            models[name] = fields
            model_modules[name] = mod.path  # module d'origine (pour vignemale gen)

        elif kind == "class":
            # table vignemale.datamodel (classe avec `__database__ = "…"`) :
            # la base déclarée pilote le provisioning, comme SQLDatabase(...)
            dbattr = m.members.get("__database__")
            if dbattr is not None and dbattr.kind.value == "attribute":
                dbname = _lit(dbattr.value)
                if isinstance(dbname, str) and dbname and dbname not in databases:
                    databases.append(dbname)

        elif kind == "attribute" and m.value is not None and type(m.value).__name__ == "ExprCall":
            fn = str(m.value.function)
            first_arg = next(
                (_lit(a) for a in m.value.arguments if type(a).__name__ != "ExprKeyword"),
                None,
            )
            if fn == "Service" or fn.endswith(".Service"):
                service = first_arg
            elif fn == "SQLDatabase" or fn.endswith(".SQLDatabase"):
                if first_arg:
                    databases.append(first_arg)
            elif fn == "Bucket" or fn.endswith(".Bucket"):
                if first_arg:
                    buckets.append(first_arg)
            elif fn == "Secret" or fn.endswith(".Secret"):
                if first_arg:
                    secrets.append(first_arg)

        elif kind == "function":
            for deco in m.decorators:
                call = deco.value
                if type(call).__name__ != "ExprCall":
                    # décorateur sans parenthèses : @auth_handler
                    dname = str(call)
                    if dname == "auth_handler" or dname.endswith(".auth_handler"):
                        auth_fn = name
                    continue
                cfn = str(call.function)
                if cfn != "api" and not cfn.endswith(".api"):
                    continue
                kw = {
                    a.name: _lit(a.value)
                    for a in call.arguments
                    if type(a).__name__ == "ExprKeyword"
                }
                request = next(
                    (str(p.annotation) for p in m.parameters
                     if p.name == "body" and p.annotation is not None),
                    None,
                )
                runtime_params = ("body", "stream", "auth", "query", "headers")
                endpoints.append({
                    "name": name,
                    "method": kw.get("method"),
                    "path": kw.get("path"),
                    "stream": bool(kw.get("stream", False)),
                    "auth": bool(kw.get("auth", False)),
                    "expose": bool(kw.get("expose", True)),
                    "request": request,
                    "response": str(m.returns) if m.returns is not None else None,
                    "params": {
                        p.name: str(p.annotation) if p.annotation is not None else None
                        for p in m.parameters
                        if p.name not in runtime_params
                    },
                })

    return service, endpoints, models, databases, auth_fn, model_modules, buckets, secrets


def extract_path(path: str) -> tuple[dict, str]:
    """Extrait un fichier OU un dossier (un service par module .py)."""
    path = os.path.abspath(path)
    services = []
    models = {}
    databases = []
    buckets = []
    secrets = []

    if os.path.isdir(path):
        app_name = os.path.basename(path)
        modnames = []
        for f in sorted(os.listdir(path)):
            full = os.path.join(path, f)
            if f.endswith(".py") and not f.startswith("_"):
                modnames.append(f[:-3])
            elif (
                os.path.isdir(full)
                and not f.startswith(("_", "."))
                and f != "vignemale_clients"  # clients générés ≠ service
                and os.path.isfile(os.path.join(full, "__init__.py"))
            ):
                modnames.append(f)  # dossier-service (package)
    else:
        app_name = os.path.splitext(os.path.basename(path))[0]
        modnames = [app_name]
        path = os.path.dirname(path)

    auth_handler = None
    model_modules = {}
    for modname in modnames:
        mod = griffe.load(modname, search_paths=[path])
        svc, eps, mods, dbs, auth_fn, modmods, bks, secs = _extract_module(mod)
        if eps or svc:
            services.append(
                {"name": svc or modname, "endpoints": eps, "databases": dbs, "buckets": bks}
            )
        models.update(mods)
        model_modules.update(modmods)
        databases.extend(db for db in dbs if db not in databases)
        buckets.extend(b for b in bks if b not in buckets)
        secrets.extend(s for s in secs if s not in secrets)
        if auth_fn and auth_handler is None:
            auth_handler = {"name": auth_fn, "service": svc or modname}

    return {
        "services": services,
        "models": models,
        "model_modules": model_modules,
        "databases": databases,
        "buckets": buckets,
        "secrets": secrets,
        "auth_handler": auth_handler,
    }, app_name


# ----- 2) dict -> vrai meta.proto (Data) -----

_BUILTIN = {
    "str": schema.STRING,
    "int": schema.INT64,
    "float": schema.FLOAT64,
    "bool": schema.BOOL,
    "bytes": schema.BYTES,
}


def _builtin(type_name):
    return _BUILTIN.get((type_name or "").strip(), schema.ANY)


def _add_path(path_msg, path_str: str) -> None:
    path_msg.type = meta.Path.URL
    for seg in (path_str or "").split("/"):
        if not seg:
            continue
        ps = path_msg.segments.add()
        if seg.startswith(":"):
            ps.type = meta.PathSegment.PARAM
            ps.value = seg[1:]
        elif seg.startswith("*"):
            ps.type = meta.PathSegment.WILDCARD
            ps.value = seg[1:]
        else:
            ps.type = meta.PathSegment.LITERAL
            ps.value = seg


def build_meta(extracted: dict, app_name: str) -> "meta.Data":
    data = meta.Data()
    data.module_path = app_name
    data.language = meta.PYTHON

    decl_ids = {}
    for i, (mname, fields) in enumerate(extracted["models"].items(), start=1):
        decl = data.decls.add()
        decl.id = i
        decl.name = mname
        st = decl.type.struct
        for fname, finfo in fields.items():
            field = st.fields.add()
            field.name = fname
            field.typ.builtin = _builtin(finfo["type"])
            field.optional = not finfo["required"]
        decl_ids[mname] = i

    for db_name in extracted.get("databases", []):
        db = data.sql_databases.add()
        db.name = db_name

    for bucket_name in extracted.get("buckets", []):
        bucket = data.buckets.add()
        bucket.name = bucket_name

    secrets = extracted.get("secrets")
    if secrets:
        pkg = data.pkgs.add()
        pkg.name = app_name
        pkg.rel_path = "."
        pkg.secrets.extend(secrets)

    auth = extracted.get("auth_handler")
    if auth:
        data.auth_handler.name = auth["name"]
        data.auth_handler.service_name = auth["service"]

    for svc_info in extracted["services"]:
        svc = data.svcs.add()
        svc.name = svc_info["name"]
        svc.rel_path = "."
        svc.databases.extend(svc_info.get("databases", []))
        # appartenance bucket → service (utilisée pour relier le bucket à son
        # service dans l'aperçu d'infra).
        for b in svc_info.get("buckets", []):
            svc.buckets.add().bucket = b
        for ep in svc_info["endpoints"]:
            rpc = svc.rpcs.add()
            rpc.name = ep["name"]
            rpc.service_name = svc_info["name"]
            # PRIVATE prime : un endpoint non exposé n'est jamais public, même
            # avec auth (il n'est joignable qu'en service-à-service).
            if not ep.get("expose", True):
                rpc.access_type = meta.RPC.PRIVATE
            elif ep.get("auth"):
                rpc.access_type = meta.RPC.AUTH
            else:
                rpc.access_type = meta.RPC.PUBLIC
            rpc.proto = meta.RPC.REGULAR
            if ep["method"]:
                rpc.http_methods.append(ep["method"])
            _add_path(rpc.path, ep["path"])
            if ep["request"] in decl_ids:
                rpc.request_schema.named.id = decl_ids[ep["request"]]
            if ep["response"] in decl_ids:
                rpc.response_schema.named.id = decl_ids[ep["response"]]
            rpc.streaming_response = bool(ep["stream"])

    return data


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print("usage: python -m vignemale_cli.collect <fichier.py|dossier> [--raw]", file=sys.stderr)
        raise SystemExit(2)

    extracted, app_name = extract_path(args[0])
    if "--raw" in sys.argv:
        print(json.dumps(extracted, indent=2, ensure_ascii=False))
    else:
        print(json_format.MessageToJson(build_meta(extracted, app_name), indent=2))
