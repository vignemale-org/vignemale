"""Clients de services, façon Encore : on importe le service, on l'appelle.

    from vignemale.clients import catalog

    item = catalog.get_item(id=7)
    order = orders.create_order(body=NewOrder(item_id=7))

Chaque attribut de ce module est un client du service du même nom ; chaque
méthode appelle l'endpoint du même nom. C'est du sucre au-dessus de
`vignemale.call` : appel direct en local, HTTP signé une fois déployé,
propagation auth + trace — même code partout.

(Les stubs typés générés depuis le meta — pour l'autocomplétion pyright —
viendront avec `vignemale gen`.)
"""

from .call import call


class ServiceClient:
    """Client d'un service : `client.endpoint(body=…, **params)`."""

    def __init__(self, service: str):
        self._service = service

    def __getattr__(self, endpoint: str):
        if endpoint.startswith("_"):
            raise AttributeError(endpoint)

        def method(body=None, **params):
            return call(self._service, endpoint, body=body, **params)

        method.__name__ = endpoint
        method.__qualname__ = f"{self._service}.{endpoint}"
        return method

    def __repr__(self) -> str:
        return f"ServiceClient({self._service!r})"


def __getattr__(service: str) -> ServiceClient:  # PEP 562
    if service.startswith("_"):
        raise AttributeError(service)
    return ServiceClient(service)
