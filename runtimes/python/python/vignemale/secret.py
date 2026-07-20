"""`Secret` primitive: a declared secret, resolved by the core (Encore-style).

    from vignemale import Secret

    OPENAI_KEY = Secret("OPENAI_API_KEY")

    @api(method="POST", path="/chat")
    def chat(body: Msg) -> dict:
        client = OpenAI(api_key=OPENAI_KEY.get())   # resolved at runtime
        ...

The secret is **declared in the code**, its value comes from the ENVIRONMENT
(never in cleartext in the source):

  1. `VIGNEMALE_SECRET_<NAME>`  (e.g. VIGNEMALE_SECRET_OPENAI_API_KEY)
  2. `<NAME>`                   (the raw environment variable, e.g. OPENAI_API_KEY)

Declarative → `vignemale check` lists the required secrets (deploy will know
which ones to inject). Resolved by the core's `secrets` module (cache, encodings).
"""

import os

from . import _core

# Declared secrets (for collect / meta — deploy knows what to inject).
_secrets: list = []


class Secret:
    def __init__(self, name: str):
        self.name = name
        _secrets.append(name)

    def _env_name(self) -> str:
        specific = f"VIGNEMALE_SECRET_{self.name.upper().replace('-', '_')}"
        return specific if specific in os.environ else self.name

    def get(self) -> str:
        """Secret value (str). Raises KeyError if not defined."""
        return self.get_bytes().decode("utf-8")

    def get_bytes(self) -> bytes:
        """Raw value (bytes) — resolved via the core (`secrets`)."""
        env = self._env_name()
        if env not in os.environ:
            raise KeyError(
                f"secret '{self.name}' not defined: set "
                f"VIGNEMALE_SECRET_{self.name.upper().replace('-', '_')} or {self.name}"
            )
        return _core.resolve_env_secret(env)

    def __repr__(self) -> str:
        return f"Secret({self.name!r})"
