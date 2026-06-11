"""Client typé du service « orders » — GÉNÉRÉ par `vignemale gen`, ne pas éditer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vignemale import call
from vignemale._genutil import validate_model

if TYPE_CHECKING:
    from orders.create import Order


def create_order(*, body: Order | dict) -> dict:
    """POST /orders"""
    return call("orders", "create_order", body=body)
