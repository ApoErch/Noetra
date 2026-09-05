import uuid

from core.agent.citations import verify_citations
from core.retrieval import RetrievalHit, RetrieverSource


def _hit(path: str, start: int, end: int) -> RetrievalHit:
    """A minimal retrieved hit for `path` covering `[start, end]`."""
    return RetrievalHit(
        file_id=uuid.uuid4(),
        path=path,
        start_line=start,
        end_line=end,
        snippet="x",
        score=1.0,
        sources=[RetrieverSource.LEXICAL],
    )


def test_backed_citation_is_kept_and_collected() -> None:
    """A citation inside a retrieved range survives and becomes a RetrievalHit for the UI."""
    hits = [_hit("a/b.py", 10, 40)]
    result = verify_citations("See [a/b.py:12-20] for details.", hits)
    assert result.text == "See [a/b.py:12-20] for details."
    assert [(c.path, c.start_line, c.end_line) for c in result.citations] == [("a/b.py", 12, 20)]
    assert result.citations[0].file_id == hits[0].file_id
    assert result.stripped == []


def test_overlap_not_containment_counts() -> None:
    """A citation that merely overlaps a retrieved range is still backed."""
    hits = [_hit("a/b.py", 10, 40)]
    result = verify_citations("[a/b.py:38-45]", hits)
    assert result.citations and result.stripped == []


def test_unbacked_citation_is_demoted_to_plain_text() -> None:
    """Citations to ranges or files never retrieved lose their brackets and are reported."""
    hits = [_hit("a/b.py", 10, 40)]
    result = verify_citations("Look at [a/b.py:50-60] and [c.py:1].", hits)
    assert result.text == "Look at a/b.py:50-60 and c.py:1."
    assert result.citations == []
    assert result.stripped == ["[a/b.py:50-60]", "[c.py:1]"]


def test_single_line_citation_and_dedup() -> None:
    """`[path:N]` cites one line, and repeating the same citation yields one entry."""
    hits = [_hit("README.md", 1, 30)]
    result = verify_citations("[README.md:12] then again [README.md:12].", hits)
    assert len(result.citations) == 1
    assert result.citations[0].start_line == result.citations[0].end_line == 12


def test_reversed_range_is_normalised() -> None:
    """`[path:30-20]` is treated as 20-30."""
    hits = [_hit("a.py", 1, 100)]
    result = verify_citations("[a.py:30-20]", hits)
    assert (result.citations[0].start_line, result.citations[0].end_line) == (20, 30)


def test_prose_without_citations_is_untouched() -> None:
    """Markdown links and inline code do not match the citation pattern."""
    text = "No citations here, just `code` and [a link](http://x)."
    result = verify_citations(text, [])
    assert result.text == text
    assert result.citations == [] and result.stripped == []
