"""Vignemale example — a **Pydantic AI** agent deployed as-is, exposed via streaming.

The key point: Vignemale reimplements NO agent logic. You write your agent
with whatever framework you want (here Pydantic AI); Vignemale exposes it over HTTP + SSE.

Without an API key → `TestModel` (simulated reply, to see the streaming).
With `ANTHROPIC_API_KEY` → a real Claude model streaming token by token.

    cd vignemale/runtimes/python && source .venv/bin/activate
    pip install pydantic-ai-slim
    python ../../examples/agent.py
    curl -N -X POST 127.0.0.1:8080/chat -d '{"prompt":"introduce yourself"}'
"""

import asyncio
import os

from vignemale.api import api, serve
from pydantic_ai import Agent

if os.environ.get("ANTHROPIC_API_KEY"):
    agent = Agent("anthropic:claude-sonnet-4-6", system_prompt="You are concise and helpful.")
else:
    from pydantic_ai.models.test import TestModel

    agent = Agent(
        TestModel(
            custom_output_text=(
                "Hello! I am a Pydantic AI agent, served by Vignemale. "
                "Vignemale does not handle my logic: it exposes me over HTTP, streams me "
                "token by token, and will deploy me on Scaleway. That's all — and that's the point."
            )
        )
    )


@api(method="GET", path="/")
def health():
    return {"status": "ok", "framework": "vignemale", "agent": "pydantic-ai"}


@api(method="POST", path="/chat", stream=True)
def chat(stream, body=None):
    # Vignemale provides `stream`; the rest is 100% the user's framework.
    prompt = (body or {}).get("prompt", "introduce yourself")
    asyncio.run(_run(prompt, stream))


async def _run(prompt: str, stream) -> None:
    async with agent.run_stream(prompt) as result:
        async for delta in result.stream_text(delta=True):
            for word in delta.split(" "):
                stream.write(word + " ")
                await asyncio.sleep(0.04)


if __name__ == "__main__":
    serve(os.environ.get("VIGNEMALE_ADDR", "127.0.0.1:8080"))
