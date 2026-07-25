import uuid

from sqlalchemy.orm import Session

from core.retrieval.fusion import reciprocal_rank_fusion
from core.retrieval.lexical import lexical_search
from core.retrieval.structural import structural_search
from core.retrieval.types import RetrievalHit, RetrieverSource

__all__ = ["search", "RetrievalHit", "RetrieverSource"]


def search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Hybrid retrieval: run every retriever, fuse the rankings with RRF, return top hits.

    The single public entry point for retrieval — the /search endpoint and, later, the M6
    agent's code_search tool both call this and nothing else. Adding the M7 semantic leg
    means appending one more list to the fusion input; no caller changes.
    """
    lexical = lexical_search(db, repository_id, query, limit)
    structural = structural_search(db, repository_id, query, limit)
    return reciprocal_rank_fusion([lexical, structural], limit=limit)
