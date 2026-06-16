"""Filtrage des endpoints par service au `serve()` (topologie « un conteneur
par service » : VIGNEMALE_SERVICE_NAME → ne servir que ce service)."""

import importlib

# `vignemale.api` (le module) est shadowé par la fonction `api` réexportée :
# on récupère le vrai module via importlib.
apimod = importlib.import_module("vignemale.api")
svcmod = importlib.import_module("vignemale.service")


def _ep(name, module):
    def wrapper():
        pass

    wrapper.__module__ = module
    # tuple endpoint : (name, method, path, wrapper, stream, auth, timeout, body_limit, expose)
    return (name, "GET", "/" + name, wrapper, False, False, None, None, True)


def _setup(monkeypatch):
    monkeypatch.setattr(
        apimod, "_endpoints", [_ep("list_orders", "orders"), _ep("list_catalog", "catalog")]
    )
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders"), ("catalog", "catalog")])


def test_mono_sert_tout(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.delenv("VIGNEMALE_SERVICE_NAME", raising=False)
    assert sorted(e[0] for e in apimod._endpoints_to_serve()) == ["list_catalog", "list_orders"]


def test_filtre_par_service(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "orders")
    assert [e[0] for e in apimod._endpoints_to_serve()] == ["list_orders"]


def test_sous_module_du_service(monkeypatch):
    # un endpoint dans un sous-module du service (catalog.items) appartient à catalog
    monkeypatch.setattr(apimod, "_endpoints", [_ep("get_item", "catalog.items")])
    monkeypatch.setattr(svcmod, "_services", [("catalog", "catalog")])
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "catalog")
    assert [e[0] for e in apimod._endpoints_to_serve()] == ["get_item"]


def test_service_inconnu_repli_sur_tout(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "inexistant")
    assert sorted(e[0] for e in apimod._endpoints_to_serve()) == ["list_catalog", "list_orders"]


def _epx(name, path, module, auth=False, expose=True):
    def wrapper():
        pass

    wrapper.__module__ = module
    return (name, "GET", path, wrapper, False, auth, None, None, expose)


def test_gateway_routes(monkeypatch):
    monkeypatch.setattr(
        apimod,
        "_endpoints",
        [
            _epx("o", "/orders", "orders", auth=True),
            _epx("i", "/items/:id", "catalog"),  # préfixe statique = /items
            _epx("a", "/admin/items", "catalog", expose=False),  # privé → exclu
        ],
    )
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders"), ("catalog", "catalog")])
    monkeypatch.setenv("VIGNEMALE_SERVICE_ORDERS", "https://o.scw")
    monkeypatch.setenv("VIGNEMALE_SERVICE_CATALOG", "https://c.scw")

    routes = apimod._gateway_routes()
    byprefix = {(p, s): (u, a) for (p, s, u, a) in routes}
    # /items/:id → préfixe /items, vers l'URL de catalog
    assert byprefix[("/items", "catalog")] == ("https://c.scw", False)
    # auth de l'endpoint propagée au niveau route
    assert byprefix[("/orders", "orders")] == ("https://o.scw", True)
    # endpoint privé jamais routé
    assert not any(p == "/admin/items" for (p, s, u, a) in routes)


def test_gateway_routes_ignore_service_sans_url(monkeypatch):
    # un service dont l'URL n'est pas connue (env absente) n'est pas routé
    monkeypatch.setattr(apimod, "_endpoints", [_epx("o", "/orders", "orders")])
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders")])
    monkeypatch.delenv("VIGNEMALE_SERVICE_ORDERS", raising=False)
    assert apimod._gateway_routes() == []
