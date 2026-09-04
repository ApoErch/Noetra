import time

from celery.utils.log import get_task_logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.ai.embeddings import embed_documents
from core.models import Chunk, Repository, RepositoryStatus

logger = get_task_logger(__name__)

# Unembedded chunks per DB round trip — and, since core/ai sends a page as one request,
# the number of inputs per API call (well under OpenAI's 2,048-inputs-per-request cap).
_PAGE_SIZE = 200


def embed_repository(db: Session, repo: Repository) -> int:
    """Embed every chunk of `repo` missing an embedding; returns how many were embedded.

    Resumable by construction: selects WHERE embedding IS NULL and commits per page, so
    re-running after a crash or provider failure continues from wherever it stopped
    instead of re-embedding chunks that already succeeded.
    """
    repo.status = RepositoryStatus.EMBEDDING
    db.commit()

    stage_start = time.perf_counter()
    total = 0
    while True:
        chunks = db.scalars(
            select(Chunk)
            .where(Chunk.repository_id == repo.id, Chunk.embedding.is_(None))
            .limit(_PAGE_SIZE)
        ).all()
        if not chunks:
            break

        page_start = time.perf_counter()
        vectors = embed_documents([chunk.embed_text for chunk in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
        db.commit()  # per-page commit — a crash mid-repo keeps every page already done

        total += len(chunks)
        logger.info(
            "repo %s: embedded %d chunks in %.2fs (%d so far)",
            repo.id, len(chunks), time.perf_counter() - page_start, total,
        )

    logger.info("repo %s: embedding stage took %.2fs (%d chunks)", repo.id, time.perf_counter() - stage_start, total)
    return total
