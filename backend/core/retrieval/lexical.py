import re
import uuid
from collections import Counter

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from core.models import Chunk, File
from core.retrieval.types import RetrievalHit, RetrieverSource

_WORD_RE = re.compile(r"\w+")
# websearch_to_tsquery keywords that are query operators, not real search terms — they'd
# be re-read as operators if we echoed them back into a relaxed query.
_OPERATOR_WORDS = {"or", "and"}

# Most chunks one file may contribute to a result list. Without this, a single large,
# loosely-matching file fills every slot with its own chunks and crowds out the file that
# actually holds the answer — measurably worse recall, since a caller who never sees a file
# cannot recover it. Elasticsearch calls this collapsing; the general idea is to trade a
# little relevance for result diversity.
_MAX_CHUNKS_PER_FILE = 2

# ts_rank multiplier for files that aren't source (`file.language IS NULL` — markdown, rst,
# JSON, LICENSE). An english-config tsvector scores English prose far above code for any
# natural-language query, so without this a question about redirects returns HISTORY.md and
# LICENSE ahead of the module that implements them. Docs stay reachable, just below code.
# This is a query-time boost, the standard way to weight document classes in search.
_NON_SOURCE_RANK_FACTOR = 0.3


def lexical_search(
    db: Session, repository_id: uuid.UUID, query: str, limit: int = 20
) -> list[RetrievalHit]:
    """Full-text search over AST-aligned chunks, ranked by ts_rank; one hit per matching chunk.

    Searches `chunks`, not whole files, so every hit arrives with the real line range of the
    function or region it covers — a file-level hit has to guess which line to cite, which
    was the single largest source of wrong citations before chunking existed.

    Two passes. websearch_to_tsquery joins bare terms with AND, so a natural-language
    question ("where are credentials encrypted before storage?") demands that every term
    appear in the same chunk and typically matches nothing at all. So: run the strict query
    first, then backfill any unused slots from a relaxed any-term query. This is query
    relaxation — Elasticsearch spells it `minimum_should_match`; Postgres has no equivalent,
    hence the second pass. Strict matches keep their positions, so precision is unchanged
    and only otherwise-empty slots get filled.
    """
    lexemes = _query_lexemes(db, query)
    hits = _ranked_chunks(db, repository_id, search_text=query, lexemes=lexemes, limit=limit)
    if len(hits) >= limit:
        return hits

    relaxed = _relaxed_query(query)
    if relaxed is None:
        return hits

    # Each pass caps per file on its own, so the cap has to be re-applied across the two
    # of them as well — otherwise a file could contribute its quota twice.
    seen = {(hit.file_id, hit.start_line, hit.end_line) for hit in hits}
    per_file = Counter(hit.file_id for hit in hits)
    for hit in _ranked_chunks(
        db, repository_id, search_text=relaxed, lexemes=lexemes, limit=limit
    ):
        key = (hit.file_id, hit.start_line, hit.end_line)
        if key in seen or per_file[hit.file_id] >= _MAX_CHUNKS_PER_FILE:
            continue
        hits.append(hit)
        per_file[hit.file_id] += 1
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
    """Ask Postgres for the query's own stemmed lexemes, so snippets use the index's vocabulary.

    to_tsvector applies exactly the stemming and stopword removal that built `content_tsv`
    ("credentials" -> 'credenti', "the" dropped). Re-deriving stems in Python would drift
    from what actually matched.
    """
    stmt = select(func.tsvector_to_array(func.to_tsvector("english", query)))
    return list(db.scalar(stmt) or [])


def _ranked_chunks(
    db: Session,
    repository_id: uuid.UUID,
    *,
    search_text: str,
    lexemes: list[str],
    limit: int,
) -> list[RetrievalHit]:
    """Run one tsquery over a repo's chunks and return each match as a hit at that chunk's line range.

    `lexemes` always comes from the user's original query, even when `search_text` is the
    relaxed rewrite — the snippet should reflect what they asked for, not the operators.
    """
    # websearch_to_tsquery parses Google-style input ("auth OR \"session cookie\"") and
    # never raises on junk — the safe choice for raw user text. `@@` is the match operator;
    # ts_rank scores how well the chunk matches, so better matches sort first. content_tsv
    # is generated over `embed_text`, so the path and enclosing class are searchable too.
    tsquery = func.websearch_to_tsquery("english", search_text)
    raw_rank = func.ts_rank(Chunk.content_tsv, tsquery)
    rank = case(
        (File.language.is_(None), raw_rank * _NON_SOURCE_RANK_FACTOR), else_=raw_rank
    ).label("rank")
    # row_number() partitioned by file ranks each file's chunks against each other, so the
    # cap is applied *before* LIMIT truncates. Capping in Python after the fact wouldn't
    # work — LIMIT would already have thrown away every file past the crowding one.
    per_file_rank = (
        func.row_number().over(partition_by=Chunk.file_id, order_by=rank.desc()).label("per_file_rank")
    )
    ranked = (
        select(
            Chunk.file_id,
            File.path,
            Chunk.start_line,
            Chunk.end_line,
            Chunk.content,
            rank,
            per_file_rank,
        )
        .join(File, File.id == Chunk.file_id)
        .where(Chunk.repository_id == repository_id)
        .where(Chunk.content_tsv.op("@@")(tsquery))
        .subquery()
    )
    stmt = (
        select(ranked)
        .where(ranked.c.per_file_rank <= _MAX_CHUNKS_PER_FILE)
        .order_by(ranked.c.rank.desc())
        .limit(limit)
    )

    return [
        RetrievalHit(
            file_id=row.file_id,
            path=row.path,
            start_line=row.start_line,  # exact — the chunk's own boundaries, nothing inferred
            end_line=row.end_line,
            snippet=_snippet(row.content, lexemes),
            score=float(row.rank),
            sources=[RetrieverSource.LEXICAL],
        )
        for row in db.execute(stmt).all()
    ]


def _snippet(content: str, lexemes: list[str]) -> str:
    """Pick the line inside a chunk that best matches the query, for the results list.

    Display only — the citation comes from the chunk's line range, so this can't put a hit
    in the wrong place the way file-level line-guessing could.
    """
    lines = content.splitlines()
    if not lines:
        return ""
    if lexemes:
        best = max(lines, key=lambda line: _line_score(line, lexemes))
        if best.strip():
            return best.strip()
    return lines[0].strip()


def _line_score(line: str, lexemes: list[str]) -> int:
    """Count how many distinct query lexemes appear in one line, matched on stem prefix.

    Prefix matching approximates the stemmer: a lexeme is a prefix of the words it came
    from ('credenti' <- "credentials"), so this agrees with what the index matched.
    """
    words = [word.lower() for word in _WORD_RE.findall(line)]
    return sum(1 for lexeme in lexemes if any(word.startswith(lexeme) for word in words))
