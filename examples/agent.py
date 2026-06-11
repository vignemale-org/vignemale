"""Exemple Vignemale — un agent **Pydantic AI** déployé tel quel, exposé en streaming.

Le point clé : Vignemale ne réimplémente AUCUNE logique d'agent. Tu écris ton agent
avec le framework que tu veux (ici Pydantic AI) ; Vignemale l'expose en HTTP + SSE.

Sans clé API → `TestModel` (réponse simulée, pour voir le streaming).
Avec `ANTHROPIC_API_KEY` → un vrai modèle Claude qui stream token par token.

    cd vignemale/runtimes/python && source .venv/bin/activate
    pip install pydantic-ai-slim
    python ../../examples/agent.py
    curl -N -X POST 127.0.0.1:8080/chat -d '{"prompt":"présente-toi"}'
"""

import asyncio
import os

from vignemale.api import api, serve
from pydantic_ai import Agent

if os.environ.get("ANTHROPIC_API_KEY"):
    agent = Agent("anthropic:claude-sonnet-4-6", system_prompt="Tu es concis et utile.")
else:
    from pydantic_ai.models.test import TestModel

    agent = Agent(
        TestModel(
            custom_output_text=(
                "Bonjour ! Je suis un agent Pydantic AI, servi par Vignemale. "
                "Vignemale ne gère pas ma logique : il m'expose en HTTP, me streame "
                "token par token, et me déploiera sur Scaleway. C'est tout — et c'est le but."
            )
        )
    )


@api(method="GET", path="/")
def health():
    return {"status": "ok", "framework": "vignemale", "agent": "pydantic-ai"}


@api(method="POST", path="/chat", stream=True)
def chat(stream, body=None):
    # Vignemale fournit `stream` ; le reste, c'est 100 % le framework de l'utilisateur.
    prompt = (body or {}).get("prompt", "présente-toi")
    asyncio.run(_run(prompt, stream))


async def _run(prompt: str, stream) -> None:
    async with agent.run_stream(prompt) as result:
        async for delta in result.stream_text(delta=True):
            for word in delta.split(" "):
                stream.write(word + " ")
                await asyncio.sleep(0.04)


if __name__ == "__main__":
    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
