"""Runtime des clients générés par `vignemale gen` (interne)."""

import importlib


def validate_model(module: str, name: str, data):
    """Re-type la réponse d'un appel inter-services dans le modèle déclaré.

    Le module du modèle est importé paresseusement : en local il est déjà
    chargé (no-op) ; déployé, le code est dans la même image (monorepo).
    """
    if not isinstance(data, dict):
        return data
    cls = getattr(importlib.import_module(module), name)
    return cls.model_validate(data)
