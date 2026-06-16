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
