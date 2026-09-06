import time
from collections.abc import Sequence
from typing import Any

from celery.utils.log import get_task_logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import CodeEntity, EntityKind, File, Language, Metric, Repository, RepositoryStatus

logger = get_task_logger(__name__)

# How many entries the largest-files metric keeps. Ten fills a dashboard panel; the full
# ordering is a file-tree question, not a metrics one.
LARGEST_FILES_LIMIT = 10


def _build_function_counts(rows: Sequence[tuple[EntityKind, int]]) -> dict[str, int]:
    """Turn `GROUP BY kind` rows into a fixed shape, so a repo with no classes reports 0 rather than omitting the key."""
    counts = {kind.value: 0 for kind in EntityKind}
    for kind, count in rows:
        counts[kind.value] = count
    return counts


def _build_language_breakdown(rows: Sequence[tuple[Language | None, int, int]]) -> list[dict[str, Any]]:
    """Shape `GROUP BY language` rows into a list ordered by lines of code, largest first.

    Drops the null-language group — every Markdown, JSON, config and binary file lands in it,
    and it is not a language. Ordering happens here because a grouped query returns rows in no
    defined order, and the dashboard renders them as a ranked bar.
    """
    breakdown = [
        {"language": language.value, "files": files, "loc": loc}
        for language, files, loc in rows
        if language is not None
    ]
    breakdown.sort(key=lambda entry: entry["loc"], reverse=True)
    return breakdown


def _build_largest_files(rows: Sequence[tuple[str, int]]) -> list[dict[str, Any]]:
    """Shape the already-ordered largest-files rows for the API.

    No language field: the frontend already derives one from the path (`web/src/lib/language.ts`),
    and `file.loc` is only ever set alongside `file.language`, so it would carry no new information.
    """
    return [{"path": path, "loc": loc} for path, loc in rows]


def compute_metrics(db: Session, repo: Repository) -> None:
    """Aggregate an indexed repo into `metric` rows, then mark it `ready` — the pipeline's last stage.

    The numbers describe the indexed snapshot, so they are computed once here rather than on
    every dashboard read: a 36,000-file repo would otherwise re-aggregate on every poll.

    One honest limitation is baked into these keys. `file.language` and `file.loc` are only
    populated for the parsed languages (worker/indexing.py), so anything derived from them —
    total LOC, the language breakdown, the largest-files list — covers *source* files only,
    not the Markdown, JSON and binary files that `file_count.total` includes. The API names
    the keys accordingly so the UI cannot quietly present source LOC as repo LOC.
    """
    repo.status = RepositoryStatus.METRICS
    db.commit()

    stage_start = time.perf_counter()

    total_files = db.scalar(select(func.count()).select_from(File).where(File.repository_id == repo.id)) or 0
    source_files = (
        db.scalar(
            select(func.count())
            .select_from(File)
            .where(File.repository_id == repo.id, File.language.is_not(None))
        )
        or 0
    )
    source_loc = (
        db.scalar(select(func.coalesce(func.sum(File.loc), 0)).where(File.repository_id == repo.id)) or 0
    )

    entity_rows = db.execute(
        select(CodeEntity.kind, func.count())
        .where(CodeEntity.repository_id == repo.id)
        .group_by(CodeEntity.kind)
    ).all()

    # The null-language group is left in the query and dropped by the shaping function, so the
    # rule about what counts as a language lives in exactly one (unit-testable) place.
    language_rows = db.execute(
        select(File.language, func.count(), func.coalesce(func.sum(File.loc), 0))
        .where(File.repository_id == repo.id)
        .group_by(File.language)
    ).all()

    # The `loc IS NOT NULL` filter is load-bearing here, not defensive: Postgres sorts NULLs
    # first on a DESC order, so without it the top ten would be ten unparsed files.
    largest_rows = db.execute(
        select(File.path, File.loc)
        .where(File.repository_id == repo.id, File.loc.is_not(None))
        .order_by(File.loc.desc())
        .limit(LARGEST_FILES_LIMIT)
    ).all()

    values: dict[str, Any] = {
        "file_count": {"total": total_files, "source": source_files},
        "function_count": _build_function_counts([(row[0], row[1]) for row in entity_rows]),
        "total_loc": {"source_loc": source_loc},
        "language_breakdown": _build_language_breakdown([(row[0], row[1], row[2]) for row in language_rows]),
        "largest_files": _build_largest_files([(row[0], row[1]) for row in largest_rows]),
    }

    # Delete-then-insert rather than an upsert: a re-index rewrites all five keys anyway, and
    # this way a key that stops being produced cannot linger as a stale row.
    db.query(Metric).filter(Metric.repository_id == repo.id).delete()
    for key, value in values.items():
        db.add(Metric(repository_id=repo.id, key=key, value=value))

    repo.status = RepositoryStatus.READY
    db.commit()

    logger.info("repo %s: metrics stage took %.2fs", repo.id, time.perf_counter() - stage_start)
