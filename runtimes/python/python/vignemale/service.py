"""Primitive `Service` : regroupe des endpoints (et, plus tard, des ressources).

Façon Encore : un module = un service.

    from vignemale import Service, api

    svc = Service("catalog")

    @api(method="GET", path="/items/:id")
    def get_item(id): ...
"""

import inspect

# Registre runtime des services déclarés : (nom, module).
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
