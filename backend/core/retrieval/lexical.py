import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import File
from core.retrieval.types import RetrievalHit, RetrieverSource

_WORD_RE = re.compile(r"\w+")
# websearch_to_tsquery keywords that are query operators, not real search terms — they'd
# be re-read as operators if we echoed them back into a relaxed query.
_OPERATOR_WORDS = {"or", "and"}

# How many lines either side of a candidate count toward its score. A query term sitting
# alone on an import line at the top of a file shouldn't outrank the block of code the
# query is actually about, so every line is scored together with its neighbours.
_CONTEXT_WINDOW_LINES = 2


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
    lexemes = _query_lexemes(db, query)
    hits = _ranked_files(db, repository_id, search_text=query, lexemes=lexemes, limit=limit)
    if len(hits) >= limit:
        return hits

    relaxed = _relaxed_query(query)
    if relaxed is None:
        return hits

    seen = {hit.file_id for hit in hits}
    for hit in _ranked_files(
        db, repository_id, search_text=relaxed, lexemes=lexemes, limit=limit
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


def _query_lexemes(db: Session, query: str) -> list[str]:
    """Ask Postgres for the query's own stemmed lexemes, so citations use the index's vocabulary.

    to_tsvector applies exactly the stemming and stopword removal that built `content_tsv`
    ("credentials" -> 'credenti', "the" dropped). Re-deriving stems in Python would drift
    from what actually matched, which is the whole bug this avoids.
    """
    stmt = select(func.tsvector_to_array(func.to_tsvector("english", query)))
    return list(db.scalar(stmt) or [])


def _ranked_files(
    db: Session,
    repository_id: uuid.UUID,
    *,
    search_text: str,
    lexemes: list[str],
    limit: int,
) -> list[RetrievalHit]:
    """Run one tsquery over a repo's files and return each match as a hit cited at its best line.

    `lexemes` always comes from the user's original query, even when `search_text` is the
    relaxed rewrite — the citation should point at what they asked for, not at the operators.
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
        line, snippet = _best_line(row.content, lexemes)
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


def _best_line(content: str | None, lexemes: list[str]) -> tuple[int, str]:
    """Find the line at the centre of the densest cluster of query matches, for a line citation.

    Full-text search matches a whole file; a citation needs a line. Source words are matched
    against Postgres' own stemmed lexemes by prefix ('credenti' matches "credentials"), since
    a stem is a prefix of the words it came from — that keeps the citation consistent with
    what the index matched. Neighbouring lines count toward each line's score so a lone term
    in an import loses to the block of code the query is really about.
    """
    if not content:
        return 1, ""
    lines = content.splitlines()
    if not lines:
        return 1, ""
    if not lexemes:
        return 1, lines[0].strip()

    scores = [_line_score(line, lexemes) for line in lines]

    best_idx, best_score = -1, 0
    for i, own in enumerate(scores):
        if own == 0:
            continue  # cite a line that actually matches, never one merely near a match
        window = scores[max(0, i - _CONTEXT_WINDOW_LINES) : i + _CONTEXT_WINDOW_LINES + 1]
        score = own * 2 + sum(window)  # the line's own terms outweigh its neighbours'
        if score > best_score:
            best_idx, best_score = i, score

    if best_idx < 0:
        # The file matched on a word form the prefix rule didn't catch (an irregular stem
        # like 'written' -> "write"). Nothing better to point at than the top of the file.
        return 1, lines[0].strip()
    return best_idx + 1, lines[best_idx].strip()


def _line_score(line: str, lexemes: list[str]) -> int:
    """Count how many distinct query lexemes appear in one line, matched on stem prefix."""
    words = [word.lower() for word in _WORD_RE.findall(line)]
    return sum(1 for lexeme in lexemes if any(word.startswith(lexeme) for word in words))
