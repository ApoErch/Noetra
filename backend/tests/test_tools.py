import uuid

from core.agent.tools import (
    READ_MAX_LINES,
    SNIPPET_MAX_CHARS,
    TOOLS_BY_NAME,
    call_key,
    clamp_window,
    format_hits,
    format_numbered,
)
from core.retrieval import RetrievalHit, RetrieverSource


def test_format_numbered_pads_to_widest_line_number() -> None:
    """Line numbers are right-aligned to the width of the last line in the window."""
    content = "\n".join(f"line {i}" for i in range(1, 13))
    assert format_numbered(content, 9, 11) == " 9| line 9\n10| line 10\n11| line 11"


def test_clamp_window_defaults_to_whole_file_within_cap() -> None:
    """No window given and a short file → the whole file, untruncated."""
    assert clamp_window(50, None, None) == (1, 50, False)


def test_clamp_window_truncates_long_reads() -> None:
    """Windows longer than READ_MAX_LINES are cut at the cap and flagged."""
    assert clamp_window(1_000, None, None) == (1, READ_MAX_LINES, True)
    assert clamp_window(1_000, 300, 900) == (300, 300 + READ_MAX_LINES - 1, True)


def test_clamp_window_clips_end_to_file_length() -> None:
    """An end line past EOF is clipped to the last line."""
    assert clamp_window(20, 15, 99) == (15, 20, False)


def test_format_hits_carries_path_and_range() -> None:
    """Every rendered hit starts with `path:start-end` — the citation the model may reuse."""
    hit = RetrievalHit(
        file_id=uuid.uuid4(),
        path="core/x.py",
        start_line=3,
        end_line=9,
        snippet="  def f():  ",
        score=0.5,
        sources=[RetrieverSource.LEXICAL, RetrieverSource.SEMANTIC],
    )
    assert format_hits([hit]) == "core/x.py:3-9 · def f(): [lexical,semantic]"


def test_call_key_is_order_independent() -> None:
    """The same call with arguments in a different order is the same key."""
    assert call_key("read_file", {"path": "a", "start_line": 1}) == call_key(
        "read_file", {"start_line": 1, "path": "a"}
    )


def test_model_facing_schemas_hide_injected_args() -> None:
    """`db` and `repository_id` never appear in what the model sees."""
    expected = {
        "code_search": {"query"},
        "read_file": {"path", "start_line", "end_line"},
        "list_dependencies": {"path", "direction"},
    }
    for name, params in expected.items():
        schema = TOOLS_BY_NAME[name].tool_call_schema.model_json_schema()
        assert set(schema["properties"]) == params, name


def test_format_numbered_handles_trailing_newline() -> None:
    """A file ending in a newline has as many lines as splitlines() says, not one more."""
    content = "a\nb\n"
    assert len(content.splitlines()) == 2
    assert format_numbered(content, 1, 2) == "1| a\n2| b"


def test_format_hits_truncates_giant_snippets() -> None:
    """A single-line SVG or minified bundle must not flood the model through a snippet."""
    hit = RetrievalHit(
        file_id=uuid.uuid4(), path="logo.svg", start_line=1, end_line=1,
        snippet="x" * 10_000, score=1.0, sources=[RetrieverSource.LEXICAL],
    )
    line = format_hits([hit])
    assert len(line) < SNIPPET_MAX_CHARS + 60 and line.endswith("[lexical]")
