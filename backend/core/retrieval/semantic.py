import uuid

from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from core.ai.embeddings import embed_query
from core.models import Chunk, File
from core.retrieval.types import MAX_CHUNKS_PER_FILE, RetrievalHit, RetrieverSource


def semantic_search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Cosine-similarity search over embedded chunks; one hit per matching chunk.

    Mirrors lexical_search's SQL shape on purpose: same per-file cap via row_number(), and
    emits the chunk's own start_line/end_line verbatim. That second part matters more than
    it looks — RRF fuses on (file_id, start_line, end_line), so if this leg emitted
    different ranges than lexical for the same chunk, the two would never dedupe and fusion
    would silently degrade into concatenation that still looks like it's working.
    """
    query_vector = embed_query(query)

    # `<=>` is pgvector's cosine-distance operator: 0 = identical, 2 = opposite. `.op()`
    # rather than `.cosine_distance()` to match lexical.py's `.op("@@")` house style and
    # stay typed — `.cosine_distance()` resolves to `Any` under mypy strict.
    distance = Chunk.embedding.op("<=>", return_type=Float)(query_vector).label("distance")
    per_file_rank = (
        func.row_number().over(partition_by=Chunk.file_id, order_by=distance).label("per_file_rank")
    )
    ranked = (
        select(
            Chunk.file_id,
            File.path,
            Chunk.start_line,
            Chunk.end_line,
            Chunk.content,
            distance,
            per_file_rank,
        )
        .join(File, File.id == Chunk.file_id)
        .where(Chunk.repository_id == repository_id)
        # NULL <=> vec is NULL, not an error — on a partially-embedded repo those rows
        # would otherwise come back and float(row.distance) would raise. Required, not
        # defensive: this repo state (mid-embedding) is the normal case, not an edge case.
        .where(Chunk.embedding.isnot(None))
        .subquery()
    )
    stmt = (
        select(ranked)
        .where(ranked.c.per_file_rank <= MAX_CHUNKS_PER_FILE)
        .order_by(ranked.c.distance)
        .limit(limit)
    )

    hits: list[RetrievalHit] = []
    for row in db.execute(stmt).all():
        hits.append(
            RetrievalHit(
                file_id=row.file_id,
                path=row.path,
                start_line=row.start_line,
                end_line=row.end_line,
                snippet=_first_line(row.content),
                score=1.0 - float(row.distance),  # cosine distance -> similarity
                sources=[RetrieverSource.SEMANTIC],
                # No match_line: on a semantic-only hit the query's words usually don't
                # appear in the chunk at all (that's the entire point of this leg), so
                # reusing lexical's line-scoring would produce a match_line that lies —
                # it'd point at the first line regardless of relevance. Fusion backfills
                # this from lexical's honest match_line when both legs hit the same chunk.
                match_line=None,
            )
        )
    return hits


def _first_line(content: str) -> str:
    """The chunk's first non-blank line — a neutral snippet when there's no matched line to show."""
    for line in content.splitlines():
        if line.strip():
            return line.strip()
    return ""
