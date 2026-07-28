import uuid
from collections import Counter

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from core.models import Chunk
from core.retrieval.fusion import reciprocal_rank_fusion
from core.retrieval.lexical import lexical_search
from core.retrieval.semantic import semantic_search
from core.retrieval.types import MAX_CHUNKS_PER_FILE, RetrievalHit, RetrieverSource

__all__ = ["search", "RetrievalHit", "RetrieverSource"]

_ALL_LEGS = (RetrieverSource.LEXICAL, RetrieverSource.SEMANTIC)


def search(
    db: Session,
    repository_id: uuid.UUID,
    query: str,
    limit: int = 20,
    legs: tuple[RetrieverSource, ...] | None = None,
) -> list[RetrievalHit]:
    """Retrieval entry point — the /search endpoint and, later, the M8 agent's code_search
    tool both call this and nothing else.

    `legs` restricts which retrievers run (default: both). This is what lets
    eval/run.py's --legs flag measure each leg's contribution directly, instead of
    hand-editing this function the way the M5 structural-retrieval ablation had to.
    """
    legs = legs if legs is not None else _ALL_LEGS

    ranked_lists: list[list[RetrievalHit]] = []
    if RetrieverSource.LEXICAL in legs:
        ranked_lists.append(lexical_search(db, repository_id, query, limit=limit * 2))

    # Gated on data (does this repo have any embedded chunks?), not on repository.status.
    # Status can sit at EMBEDDING indefinitely — a repo that never finishes, or simply
    # hasn't reached M9's metrics stage yet — so it can't answer "is semantic usable right
    # now" the way a direct data check can (docs/RETRIEVAL.md).
    if RetrieverSource.SEMANTIC in legs and _has_embedded_chunks(db, repository_id):
        try:
            ranked_lists.append(semantic_search(db, repository_id, query, limit=limit * 2))
        except Exception:
            # A missing API key or an exhausted retry degrades to whatever other legs
            # are running rather than 500ing the whole search — same principle as
            # embedding failure not bricking an otherwise-searchable repo.
            pass

    if not ranked_lists:
        return []
    if len(ranked_lists) == 1:
        hits = ranked_lists[0]
    else:
        # Lexical first: RRF's *scores* are order-independent, but fusion.py's merge is
        # first-wins on metadata, so lexical's real match_line survives over semantic's
        # None whenever both legs return the same chunk.
        hits = reciprocal_rank_fusion(ranked_lists, limit=limit * 2)

    return _cap_per_file(hits, limit=limit)


def _has_embedded_chunks(db: Session, repository_id: uuid.UUID) -> bool:
    """Whether this repo has at least one embedded chunk — semantic_search is worth calling only if so."""
    stmt = select(exists().where(Chunk.repository_id == repository_id, Chunk.embedding.isnot(None)))
    return bool(db.scalar(stmt))


def _cap_per_file(hits: list[RetrievalHit], *, limit: int) -> list[RetrievalHit]:
    """Re-apply the per-file cap across a (possibly fused) list, then truncate to `limit`.

    Each leg already caps itself internally, but a file at its cap in *both* lexical and
    semantic can still land 4 times after fusion — the same crowding problem
    lexical_search's own two-pass cap exists for, one level up.
    """
    per_file: Counter[uuid.UUID] = Counter()
    capped: list[RetrievalHit] = []
    for hit in hits:
        if per_file[hit.file_id] >= MAX_CHUNKS_PER_FILE:
            continue
        capped.append(hit)
        per_file[hit.file_id] += 1
        if len(capped) == limit:
            break
    return capped
