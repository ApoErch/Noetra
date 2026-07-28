import time

from celery.utils.log import get_task_logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.ai.embeddings import embed_documents
from core.models import Chunk, Repository, RepositoryStatus

logger = get_task_logger(__name__)

# How many unembedded chunks to pull per DB round trip. core/ai's own batching further
# splits this into API-request-sized pieces by token budget — this is just the page size
# for "how much work to grab before pacing," not the size of any single Gemini request.
_PAGE_SIZE = 200
# Sleep between pages so a big repo doesn't fire requests back-to-back and blow through
# the per-minute quota. Backoff (in core/ai) reacts to a 429 already happening; this is
# what avoids triggering it in the first place.
_PACE_SECONDS = 2.0


def embed_repository(db: Session, repo: Repository) -> int:
    """Embed every chunk of `repo` missing an embedding; returns how many were embedded.

    Resumable by construction: selects WHERE embedding IS NULL and commits per page, so
    re-running after a crash or an exhausted retry continues from wherever it stopped
    instead of re-embedding chunks that already succeeded.
    """
    repo.status = RepositoryStatus.EMBEDDING
    db.commit()

    total = 0
    while True:
        chunks = db.scalars(
            select(Chunk)
            .where(Chunk.repository_id == repo.id, Chunk.embedding.is_(None))
            .limit(_PAGE_SIZE)
        ).all()
        if not chunks:
            break

        vectors = embed_documents([chunk.embed_text for chunk in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector
        db.commit()  # per-page commit — a crash mid-repo keeps every page already done

        total += len(chunks)
        logger.info("repo %s: embedded %d chunks (%d so far)", repo.id, len(chunks), total)
        time.sleep(_PACE_SECONDS)

    return total
