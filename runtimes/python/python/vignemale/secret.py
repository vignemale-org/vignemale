"""Primitive `Secret` : un secret déclaré, résolu par le core (façon Encore).

    from vignemale import Secret

    OPENAI_KEY = Secret("OPENAI_API_KEY")

    @api(method="POST", path="/chat")
    def chat(body: Msg) -> dict:
        client = OpenAI(api_key=OPENAI_KEY.get())   # résolu au runtime
        ...

Le secret est **déclaré dans le code**, sa valeur vient de l'ENVIRONNEMENT
(jamais en clair dans le source) :

  1. `VIGNEMALE_SECRET_<NOM>`  (ex. VIGNEMALE_SECRET_OPENAI_API_KEY)
  2. `<NOM>`                   (la variable d'environnement brute, ex. OPENAI_API_KEY)

Déclaratif → `vignemale check` liste les secrets requis (le deploy saura
lesquels injecter). Résolu par le module `secrets` du core (cache, encodages).
"""

import os

from . import _core

# Secrets déclarés (pour collect / meta — le deploy sait quoi injecter).
_secrets: list = []


class Secret:
    def __init__(self, name: str):
        self.name = name
        _secrets.append(name)

    def _env_name(self) -> str:
        specific = f"VIGNEMALE_SECRET_{self.name.upper().replace('-', '_')}"
        return specific if specific in os.environ else self.name

    def get(self) -> str:
        """Valeur du secret (str). Lève KeyError si non défini."""
        return self.get_bytes().decode("utf-8")

    def get_bytes(self) -> bytes:
        """Valeur brute (bytes) — résolue via le core (`secrets`)."""
        env = self._env_name()
        if env not in os.environ:
            raise KeyError(
                f"secret '{self.name}' non défini : pose "
                f"VIGNEMALE_SECRET_{self.name.upper().replace('-', '_')} ou {self.name}"
            )
        return _core.resolve_env_secret(env)

    def __repr__(self) -> str:
        return f"Secret({self.name!r})"
