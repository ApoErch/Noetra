from core.models import EntityKind, Language
from worker.metrics import _build_function_counts, _build_language_breakdown, _build_largest_files


def test_function_counts_fill_in_kinds_the_repo_has_none_of() -> None:
    """A repo with no classes reports classes: 0 rather than omitting the key, so the dashboard never renders a blank."""
    counts = _build_function_counts([(EntityKind.FUNCTION, 12), (EntityKind.METHOD, 5)])
    assert counts == {"function": 12, "class": 0, "method": 5}


def test_language_breakdown_orders_by_lines_of_code() -> None:
    """A grouped query returns rows in no defined order, so the shaping step ranks them largest first."""
    breakdown = _build_language_breakdown(
        [(Language.JAVASCRIPT, 40, 900), (Language.PYTHON, 12, 4_200), (Language.TYPESCRIPT, 3, 1_100)]
    )
    assert [entry["language"] for entry in breakdown] == ["python", "typescript", "javascript"]
    assert breakdown[0] == {"language": "python", "files": 12, "loc": 4_200}


def test_language_breakdown_drops_the_unparsed_group() -> None:
    """Markdown, JSON and binaries all group under a null language; that bucket is not a language."""
    breakdown = _build_language_breakdown([(None, 57, 0), (Language.PYTHON, 12, 4_200)])
    assert breakdown == [{"language": "python", "files": 12, "loc": 4_200}]


def test_language_breakdown_is_empty_when_nothing_was_parsed() -> None:
    """A repo of only Markdown and JSON reports no languages at all — not a row of zeros."""
    assert _build_language_breakdown([(None, 57, 0)]) == []


def test_largest_files_keeps_the_order_the_query_produced() -> None:
    """The rows arrive already sorted and capped by the query; shaping must not reorder them."""
    largest = _build_largest_files([("src/app.py", 800), ("src/util.py", 120)])
    assert largest == [{"path": "src/app.py", "loc": 800}, {"path": "src/util.py", "loc": 120}]
