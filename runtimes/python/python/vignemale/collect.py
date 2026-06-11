"""Extracteur STATIQUE du graphe `meta` — via griffe, **sans exécuter l'app**.

Parse le source (décorateurs `@api`, `Service(...)`, annotations, modèles Pydantic)
et construit le **vrai `meta.proto`** (`Data` : services · rpcs · decls), émis en
protojson — le graphe canonique, diffable dans une PR.

    python -m vignemale.collect ../../examples/typed.py       # un fichier
    python -m vignemale.collect ../../examples/shop           # un dossier (multi-service)
    python -m vignemale.collect ... --raw                     # dict intermédiaire
"""

import ast
import json
import os
import sys

import griffe
from google.protobuf import json_format

from vignemale.parser.meta.v1 import meta_pb2 as meta
from vignemale.parser.schema.v1 import schema_pb2 as schema


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
    """Renvoie (service, endpoints, models, databases, auth_handler) pour un module griffe."""
    service = None
    models = {}
    endpoints = []
    databases = []
    auth_fn = None

    for name, m in mod.members.items():
        kind = m.kind.value

        if kind == "class" and _is_pydantic(m):
            fields = {}
            for attr_name, attr in m.members.items():
                if attr.kind.value == "attribute":
                    fields[attr_name] = {
                        "type": str(attr.annotation) if attr.annotation is not None else None,
                        "required": attr.value is None,
                        "default": _lit(attr.value),
                    }
            models[name] = fields

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
                endpoints.append({
                    "name": name,
                    "method": kw.get("method"),
                    "path": kw.get("path"),
                    "stream": bool(kw.get("stream", False)),
                    "auth": bool(kw.get("auth", False)),
                    "request": request,
                    "response": str(m.returns) if m.returns is not None else None,
                })

    return service, endpoints, models, databases, auth_fn


def extract_path(path: str) -> tuple[dict, str]:
    """Extrait un fichier OU un dossier (un service par module .py)."""
    path = os.path.abspath(path)
    services = []
    models = {}
    databases = []

    if os.path.isdir(path):
        app_name = os.path.basename(path)
        files = [f for f in sorted(os.listdir(path)) if f.endswith(".py") and not f.startswith("_")]
    else:
        app_name = os.path.splitext(os.path.basename(path))[0]
        files = [os.path.basename(path)]
        path = os.path.dirname(path)

    auth_handler = None
    for f in files:
        modname = f[:-3]
        mod = griffe.load(modname, search_paths=[path])
        svc, eps, mods, dbs, auth_fn = _extract_module(mod)
        if eps or svc:
            services.append({"name": svc or modname, "endpoints": eps, "databases": dbs})
        models.update(mods)
        databases.extend(db for db in dbs if db not in databases)
        if auth_fn and auth_handler is None:
            auth_handler = {"name": auth_fn, "service": svc or modname}

    return {
        "services": services,
        "models": models,
        "databases": databases,
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

    auth = extracted.get("auth_handler")
    if auth:
        data.auth_handler.name = auth["name"]
        data.auth_handler.service_name = auth["service"]

    for svc_info in extracted["services"]:
        svc = data.svcs.add()
        svc.name = svc_info["name"]
        svc.rel_path = "."
        svc.databases.extend(svc_info.get("databases", []))
        for ep in svc_info["endpoints"]:
            rpc = svc.rpcs.add()
            rpc.name = ep["name"]
            rpc.service_name = svc_info["name"]
            rpc.access_type = meta.RPC.AUTH if ep.get("auth") else meta.RPC.PUBLIC
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
        print("usage: python -m vignemale.collect <fichier.py|dossier> [--raw]", file=sys.stderr)
        raise SystemExit(2)

    extracted, app_name = extract_path(args[0])
    if "--raw" in sys.argv:
        print(json.dumps(extracted, indent=2, ensure_ascii=False))
    else:
        print(json_format.MessageToJson(build_meta(extracted, app_name), indent=2))
