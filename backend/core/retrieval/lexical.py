import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import File
from core.retrieval.types import RetrievalHit, RetrieverSource

_WORD_RE = re.compile(r"\w+")
# websearch_to_tsquery keywords that are query operators, not real search terms — we
# strip them before locating the matching line so "auth OR login" doesn't hunt for "or".
_OPERATOR_WORDS = {"or", "and"}


def lexical_search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Full-text search over file content, ranked by ts_rank; one hit per matching file.

    Two passes. websearch_to_tsquery joins bare terms with AND, so a natural-language
    question ("where are credentials encrypted before storage?") demands that every term
    appear in the same file and typically matches nothing at all. So: run the strict query
    first, then backfill any unused slots from a relaxed any-term query. This is query
    relaxation — Elasticsearch spells it `minimum_should_match`; Postgres has no equivalent,
    hence the second pass. Strict matches keep their positions, so precision is unchanged
    and only otherwise-empty slots get filled.
    """
    hits = _ranked_files(db, repository_id, search_text=query, snippet_query=query, limit=limit)
    if len(hits) >= limit:
        return hits

    relaxed = _relaxed_query(query)
    if relaxed is None:
        return hits

    seen = {hit.file_id for hit in hits}
    for hit in _ranked_files(
        db, repository_id, search_text=relaxed, snippet_query=query, limit=limit
    ):
        if hit.file_id in seen:
            continue
        hits.append(hit)
        if len(hits) == limit:
            break
    return hits


def _relaxed_query(query: str) -> str | None:
    """Rewrite a query so any term can match instead of all of them; None if it has under two terms.

    Built in websearch syntax rather than raw tsquery `|` operators so websearch_to_tsquery
    still does the stemming and stopword removal, and still never raises on junk input.
    """
    terms = [t for t in _WORD_RE.findall(query) if t.lower() not in _OPERATOR_WORDS]
    if len(terms) < 2:
        return None
    return " OR ".join(terms)


def _ranked_files(
    db: Session,
    repository_id: uuid.UUID,
    *,
    search_text: str,
    snippet_query: str,
    limit: int,
) -> list[RetrievalHit]:
    """Run one tsquery over a repo's files and return each match as a hit cited at its best line.

    `snippet_query` stays the user's original text even when `search_text` is the relaxed
    rewrite — the citation should point at what they actually asked for, not at the operators.
    """
    # websearch_to_tsquery parses Google-style input ("auth OR \"session cookie\"") and
    # never raises on junk — the safe choice for raw user text. `@@` is the match operator;
    # ts_rank scores how well the document matches, so better matches sort first.
    tsquery = func.websearch_to_tsquery("english", search_text)
    rank = func.ts_rank(File.content_tsv, tsquery).label("rank")
    stmt = (
        select(File.id, File.path, File.content, rank)
        .where(File.repository_id == repository_id)
        .where(File.content_tsv.op("@@")(tsquery))
        .order_by(rank.desc())
        .limit(limit)
    )

    hits: list[RetrievalHit] = []
    for row in db.execute(stmt).all():
        line, snippet = _best_line(row.content, snippet_query)
        hits.append(
            RetrievalHit(
                file_id=row.id,
                path=row.path,
                start_line=line,
                end_line=line,
                snippet=snippet,
                score=float(row.rank),
                sources=[RetrieverSource.LEXICAL],
            )
        )
    return hits


def _best_line(content: str | None, query: str) -> tuple[int, str]:
    """Find the file line containing the most query terms, for a line-level citation.

    Full-text search matches a whole file; a citation needs a line. We scan for the query
    words and return the first line that contains the most of them (1-indexed) plus its text.
    """
    if not content:
        return 1, ""
    terms = {t.lower() for t in _WORD_RE.findall(query)} - _OPERATOR_WORDS
    if not terms:
        return 1, content.splitlines()[0].strip() if content.strip() else ""

    lines = content.splitlines()
    best_idx, best_score = 0, -1
    for i, line in enumerate(lines):
        low = line.lower()
        score = sum(1 for term in terms if term in low)
        if score > best_score:
            best_idx, best_score = i, score
            if score == len(terms):  # every term on one line — can't beat this
                break
    return best_idx + 1, lines[best_idx].strip()
