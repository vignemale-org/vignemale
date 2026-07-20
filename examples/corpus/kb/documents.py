"""Indexing: upload (PDF or text) → extraction → chunks → embeddings →
pgvector, all within ONE transaction. And the vector search filtered by
permissions — the access filter is IN the SQL query itself.
"""

import base64
from typing import Optional

from pydantic import BaseModel

from vignemale import APIError, api, log
from vignemale.datamodel import Table

from embedding import DIM, chunk_text, embed, to_pgvector

from .bases import KnowledgeBase, accessible_kb_ids, db


class Document(Table):
    __database__ = "corpus_kb"

    id: Optional[int] = None
    kb_id: int
    filename: str
    chunks: int = 0


# the chunks table carries a pgvector `vector` → raw SQL (outside the ORM)
Document.ensure_table()
db.execute("CREATE EXTENSION IF NOT EXISTS vector")
db.execute(
    f"""
    CREATE TABLE IF NOT EXISTS chunks (
        id          BIGSERIAL PRIMARY KEY,
        document_id BIGINT NOT NULL,
        kb_id       BIGINT NOT NULL,
        seq         BIGINT NOT NULL,
        content     TEXT NOT NULL,
        embedding   vector({DIM}) NOT NULL
    )
    """
)


class NewDocument(BaseModel):
    filename: str
    content_b64: str  # PDF or text (UTF-8), base64-encoded


class VectorQuery(BaseModel):
    embedding: list
    k: int = 5


def _extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        try:
            from io import BytesIO

            from pypdf import PdfReader
        except ImportError:
            raise APIError.unimplemented(
                "PDF extraction: `pip install pypdf`"
            ) from None
        reader = PdfReader(BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise APIError.invalid_argument(
            f"{filename}: neither a PDF nor UTF-8 text"
        ) from None


@api(method="POST", path="/kbs/:id/documents", auth=True, body_limit=20 * 1024 * 1024)
def upload_document(id, body: NewDocument, auth) -> dict:
    kb_id = int(id)
    if kb_id not in accessible_kb_ids(auth):
        raise APIError.permission_denied("no access to this knowledge base")

    text = _extract_text(body.filename, base64.b64decode(body.content_b64))
    chunks = chunk_text(text)
    if not chunks:
        raise APIError.invalid_argument(f"{body.filename}: no text extracted")

    # document + chunks + embeddings: atomic
    with db.transaction() as tx:
        doc = tx.query_row(
            "INSERT INTO documents (kb_id, filename, chunks) "
            "VALUES ($1, $2, $3) RETURNING id",
            kb_id,
            body.filename,
            len(chunks),
        )
        for seq, content in enumerate(chunks):
            tx.execute(
                "INSERT INTO chunks (document_id, kb_id, seq, content, embedding) "
                "VALUES ($1, $2, $3, $4, $5::text::vector)",
                doc["id"],
                kb_id,
                seq,
                content,
                to_pgvector(embed(content)),
            )

    log.info(
        "document indexed",
        document_id=doc["id"], kb_id=kb_id, filename=body.filename,
        chunks=len(chunks),
    )
    return {"document_id": doc["id"], "filename": body.filename, "chunks": len(chunks)}


@api(method="POST", path="/vector-search", auth=True)
def vector_search(body: VectorQuery, auth) -> dict:
    """Vector search **filtered by the caller's permissions**:
    only accessible KBs (owned or via a group) are queried — the
    filter is in the WHERE, not after the fact."""
    kb_ids = accessible_kb_ids(auth)
    if not kb_ids:
        return {"results": []}
    rows = db.query(
        """
        SELECT c.content, c.seq, d.filename, k.name AS kb, k.id AS kb_id,
               round((1 - (c.embedding <=> $1::text::vector))::numeric, 4) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN kbs k ON k.id = c.kb_id
        WHERE c.kb_id = ANY(string_to_array($2, ',')::bigint[])
        ORDER BY c.embedding <=> $1::text::vector
        LIMIT $3
        """,
        to_pgvector(body.embedding),
        ",".join(str(i) for i in kb_ids),
        body.k,
    )
    return {"results": rows}
