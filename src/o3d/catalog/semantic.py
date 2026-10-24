"""Optional local, CPU-only embeddings. No external embedding API or credentials."""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from .db import EMBEDDING_MODEL, Catalog


@lru_cache(maxsize=1)
def model():
    try:
        from fastembed import TextEmbedding
    except ImportError as error:
        raise ValueError("Install oxygen-cad[catalog-semantic] to use local semantic search") from error
    threads = int(os.environ.get("O3D_CATALOG_EMBED_THREADS", "2"))
    if not 1 <= threads <= 16:
        raise ValueError("O3D_CATALOG_EMBED_THREADS must be 1..16")
    return TextEmbedding(model_name=EMBEDDING_MODEL, threads=threads)


def embed_query(text: str) -> list[float]:
    return next(model().query_embed(text)).tolist()


def embed(catalog: Catalog, limit: int = 500, batch_size: int = 64):
    if not 1 <= limit <= 100000:
        raise ValueError("Embedding limit must be 1..100000")
    rows = catalog.conn.execute("""SELECT p.id,p.search_text FROM parts p LEFT JOIN embeddings e
        ON e.part_id=p.id AND e.model=%s WHERE e.part_id IS NULL ORDER BY p.id LIMIT %s""", (EMBEDDING_MODEL, limit)).fetchall()
    count = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        vectors = list(model().passage_embed([r["search_text"] for r in batch]))
        with catalog.conn.transaction():
            for row, vector in zip(batch, vectors, strict=True):
                # Lock and recheck content: a concurrent ingestion cannot attach an obsolete vector.
                current = catalog.conn.execute("SELECT search_text FROM parts WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
                if not current or current["search_text"] != row["search_text"]:
                    continue
                catalog.conn.execute("""INSERT INTO embeddings(part_id,model,content_sha256,embedding)
                    VALUES (%s,%s,%s,%s::vector) ON CONFLICT(part_id) DO UPDATE SET model=excluded.model,
                    content_sha256=excluded.content_sha256,embedding=excluded.embedding,updated_at=now()""",
                    (row["id"], EMBEDDING_MODEL, hashlib.sha256(row["search_text"].encode()).hexdigest(),
                     "[" + ",".join(str(float(v)) for v in vector) + "]"))
                count += 1
    return {"embedded": count, "model": EMBEDDING_MODEL, "dimensions": 384}
