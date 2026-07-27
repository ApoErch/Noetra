import uuid

from sqlalchemy.orm import Session

from core.retrieval.lexical import lexical_search
from core.retrieval.types import RetrievalHit, RetrieverSource

__all__ = ["search", "RetrievalHit", "RetrieverSource"]


def search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Retrieval entry point — the /search endpoint and, later, the M6 agent's code_search
    tool both call this and nothing else.

    Lexical only for now. Structural (trigram lookup over code_entity) was built and
    measured in the M5 eval: pulled out via ablation, it moved symbol-bucket recall@5 from
    0.93 to 1.00 (one question, `$ZodRegistry` — a name the text-search tokenizer mangles)
    at the cost of a second query per search. Not worth it, so it was removed (see
    docs/RETRIEVAL.md). When the M7 semantic leg lands, this goes back to
    `reciprocal_rank_fusion([lexical, semantic], limit=limit)`.
    """
    return lexical_search(db, repository_id, query, limit)
