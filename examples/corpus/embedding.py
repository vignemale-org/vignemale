"""Embeddings et découpage — partagés par les services `kb` et `rag`.

Embedding « hash bag-of-words » : déterministe, hors-ligne, suffisant pour
la démo (similarité cosinus par recouvrement de vocabulaire). Pour la prod,
remplace `embed()` par un vrai modèle (Mistral embed, OpenAI, BGE…) — une
seule fonction à changer, la dimension est paramétrable.
"""

import hashlib
import math
import re

DIM = 256


def embed(text: str) -> list:
    """Texte → vecteur l2-normalisé de dimension DIM."""
    v = [0.0] * DIM
    for token in re.findall(r"\w+", text.lower()):
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0
    norme = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norme, 6) for x in v]


def to_pgvector(vec: list) -> str:
    """Format texte pgvector : '[0.1,0.2,…]' (casté `::vector` côté SQL)."""
    return "[" + ",".join(str(x) for x in vec) + "]"


def chunk_text(text: str, size: int = 600) -> list:
    """Découpe par paragraphes, regroupés jusqu'à ~`size` caractères."""
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
