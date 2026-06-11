"""Client typé du service « rag » — GÉNÉRÉ par `vignemale gen`, ne pas éditer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vignemale import call
from vignemale._genutil import validate_model

if TYPE_CHECKING:
    from rag.search import Question


def search(*, body: Question | dict) -> dict:
    """POST /search"""
    return call("rag", "search", body=body)
