"""Client typé du service « catalog » — GÉNÉRÉ par `vignemale gen`, ne pas éditer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vignemale import call
from vignemale._genutil import validate_model

if TYPE_CHECKING:
    from catalog.items import Item


def get_item(*, id: Any) -> Item:
    """GET /items/:id"""
    return validate_model("catalog.items", "Item", call("catalog", "get_item", id=id))
