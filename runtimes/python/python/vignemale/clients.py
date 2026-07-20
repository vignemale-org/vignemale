"""Service clients, Encore style: import the service, call it.

    from vignemale.clients import catalog

    item = catalog.get_item(id=7)
    order = orders.create_order(body=NewOrder(item_id=7))

Each attribute of this module is a client for the service of the same name;
each method calls the endpoint of the same name. It is sugar on top of
`vignemale.call`: direct call locally, signed HTTP once deployed,
auth + trace propagation — same code everywhere.

(The typed stubs generated from the meta — for pyright autocompletion —
will come with `vignemale gen`.)
"""

from .call import call


class ServiceClient:
    """Client for a service: `client.endpoint(body=…, **params)`."""

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
