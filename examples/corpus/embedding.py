"""Embeddings and chunking — shared by the `kb` and `rag` services.

"Hash bag-of-words" embedding: deterministic, offline, good enough for
the demo (cosine similarity by vocabulary overlap). For production,
replace `embed()` with a real model (Mistral embed, OpenAI, BGE…) — a
single function to change, the dimension is configurable.
"""

import hashlib
import math
import re

DIM = 256


def embed(text: str) -> list:
    """Text → l2-normalized vector of dimension DIM."""
    v = [0.0] * DIM
    for token in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    norme = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norme, 6) for x in v]


def to_pgvector(vec: list) -> str:
    """pgvector text format: '[0.1,0.2,…]' (cast `::vector` on the SQL side)."""
    return "[" + ",".join(str(x) for x in vec) + "]"


def chunk_text(text: str, size: int = 600) -> list:
    """Split by paragraphs, grouped up to ~`size` characters."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, courant = [], ""
    for p in paras:
        if courant and len(courant) + len(p) + 2 > size:
            chunks.append(courant)
            courant = p
        else:
            courant = f"{courant}\n\n{p}" if courant else p
    if courant:
        chunks.append(courant)
    return chunks or ([text[:size]] if text.strip() else [])
