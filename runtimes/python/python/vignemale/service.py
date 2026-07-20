"""`Service` primitive: groups endpoints (and, later, resources).

Encore-style: one module = one service.

    from vignemale import Service, api

    svc = Service("catalog")

    @api(method="GET", path="/items/:id")
    def get_item(id): ...
"""

import inspect

# Runtime registry of declared services: (name, module).
_services: list[tuple[str, str]] = []


class Service:
    def __init__(self, name: str):
        self.name = name
        try:
            self.module = inspect.stack()[1].frame.f_globals.get("__name__", "?")
        except Exception:
            self.module = "?"
        _services.append((name, self.module))

    def __repr__(self) -> str:
        return f"Service({self.name!r})"
