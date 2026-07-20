"""Endpoint filtering by service at `serve()` (topology "one container
per service": VIGNEMALE_SERVICE_NAME → serve only that service)."""

import importlib

# `vignemale.api` (the module) is shadowed by the re-exported `api` function:
# we get the real module via importlib.
apimod = importlib.import_module("vignemale.api")
svcmod = importlib.import_module("vignemale.service")


def _ep(name, module):
    def wrapper():
        pass

    wrapper.__module__ = module
    # endpoint tuple: (name, method, path, wrapper, stream, auth, timeout, body_limit, expose)
    return (name, "GET", "/" + name, wrapper, False, False, None, None, True)


def _setup(monkeypatch):
    monkeypatch.setattr(
        apimod, "_endpoints", [_ep("list_orders", "orders"), _ep("list_catalog", "catalog")]
    )
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders"), ("catalog", "catalog")])


def test_mono_serves_everything(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.delenv("VIGNEMALE_SERVICE_NAME", raising=False)
    assert sorted(e[0] for e in apimod._endpoints_to_serve()) == ["list_catalog", "list_orders"]


def test_filter_by_service(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "orders")
    assert [e[0] for e in apimod._endpoints_to_serve()] == ["list_orders"]


def test_submodule_of_the_service(monkeypatch):
    # an endpoint in a submodule of the service (catalog.items) belongs to catalog
    monkeypatch.setattr(apimod, "_endpoints", [_ep("get_item", "catalog.items")])
    monkeypatch.setattr(svcmod, "_services", [("catalog", "catalog")])
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "catalog")
    assert [e[0] for e in apimod._endpoints_to_serve()] == ["get_item"]


def test_unknown_service_falls_back_to_everything(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("VIGNEMALE_SERVICE_NAME", "nonexistent")
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
            _epx("i", "/items/:id", "catalog"),  # static prefix = /items
            _epx("a", "/admin/items", "catalog", expose=False),  # private → excluded
        ],
    )
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders"), ("catalog", "catalog")])
    monkeypatch.setenv("VIGNEMALE_SERVICE_ORDERS", "https://o.scw")
    monkeypatch.setenv("VIGNEMALE_SERVICE_CATALOG", "https://c.scw")

    routes = apimod._gateway_routes()
    byprefix = {(p, s): (u, a) for (p, s, u, a) in routes}
    # /items/:id → /items prefix, to catalog's URL
    assert byprefix[("/items", "catalog")] == ("https://c.scw", False)
    # endpoint auth propagated to the route level
    assert byprefix[("/orders", "orders")] == ("https://o.scw", True)
    # private endpoint never routed
    assert not any(p == "/admin/items" for (p, s, u, a) in routes)


def test_gateway_routes_ignore_service_without_url(monkeypatch):
    # a service whose URL is unknown (env missing) is not routed
    monkeypatch.setattr(apimod, "_endpoints", [_epx("o", "/orders", "orders")])
    monkeypatch.setattr(svcmod, "_services", [("orders", "orders")])
    monkeypatch.delenv("VIGNEMALE_SERVICE_ORDERS", raising=False)
    assert apimod._gateway_routes() == []
