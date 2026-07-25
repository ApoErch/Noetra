import uuid

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from core.models import CodeEntity, File
from core.retrieval.types import RetrievalHit, RetrieverSource


def structural_search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Symbol lookup over code_entities: exact name match plus trigram-fuzzy match."""
    # `name % :query` is pg_trgm's trigram-similarity operator — it's what the GIN
    # trigram index accelerates, so "createTok" still finds "createToken". An exact
    # name match is forced to the top score of 1.0; everything else is ranked by how
    # similar its name is to the query.
    similarity = func.similarity(CodeEntity.name, query)
    score = case((CodeEntity.name == query, 1.0), else_=similarity).label("score")
    stmt = (
        select(CodeEntity, File.path, score)
        .join(File, File.id == CodeEntity.file_id)
        .where(CodeEntity.repository_id == repository_id)
        .where(or_(CodeEntity.name == query, CodeEntity.name.op("%")(query)))
        .order_by(score.desc())
        .limit(limit)
    )

    hits: list[RetrievalHit] = []
    for row in db.execute(stmt).all():
        entity: CodeEntity = row[0]
        hits.append(
            RetrievalHit(
                file_id=entity.file_id,
                path=row.path,
                start_line=entity.start_line,  # exact — straight from the symbol table
                end_line=entity.end_line,
                snippet=entity.signature,
                score=float(row.score),
                sources=[RetrieverSource.STRUCTURAL],
                entity_name=entity.name,
                entity_kind=entity.kind,
            )
        )
    return hits
