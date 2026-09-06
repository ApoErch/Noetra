from indexer.calls import (
    CONFIDENCE_IMPORTED_FILE,
    CONFIDENCE_SAME_FILE,
    CONFIDENCE_UNIQUE_REPO_WIDE,
    ExtractedCall,
    enclosing_entity_index,
    extract_calls,
    resolve_calls,
)
from indexer.parser import ExtractedEntity, extract

PY_SOURCE = """\
from helpers import helper


class Service:
    def run(self):
        helper()
        self.step()
        util.finalize(1)

    def step(self):
        pass
"""

TS_SOURCE = """\
import * as util from "./util";

export const run = () => {
  helper();
  util.finalize(1);
  new Thing();
};
"""


def _entity(name: str, start: int, end: int, kind: str = "function") -> ExtractedEntity:
    """An ExtractedEntity with only the fields resolution actually reads."""
    return ExtractedEntity(kind=kind, name=name, signature="", start_line=start, end_line=end)


def test_extract_calls_descends_into_function_bodies() -> None:
    """The call walk sees call sites inside a method body — exactly where parser.extract stops."""
    calls = extract_calls(PY_SOURCE, "svc.py")
    assert calls is not None
    assert (("helper", 6, None)) in [(c.callee, c.line, c.receiver) for c in calls]


def test_extract_calls_separates_receiver_kinds() -> None:
    """A bare call, a self call and a qualified call are distinguishable after extraction."""
    calls = {c.callee: c for c in extract_calls(PY_SOURCE, "svc.py") or []}
    assert calls["helper"].receiver is None
    assert calls["step"].receiver == "self"
    assert calls["finalize"].receiver == "util"


def test_extract_calls_handles_typescript_and_new_expressions() -> None:
    """JS/TS member calls and `new X()` both yield a name — instantiation is a real reference."""
    calls = {c.callee: c.receiver for c in extract_calls(TS_SOURCE, "run.ts") or []}
    assert calls["helper"] is None
    assert calls["finalize"] == "util"
    assert calls["Thing"] is None


def test_extract_calls_drops_dynamic_receivers() -> None:
    """A call on the result of another call cannot be resolved by name, so it is not extracted."""
    calls = extract_calls("def f():\n    obj.method().chained()\n", "d.py")
    assert calls is not None
    assert "chained" not in {c.callee for c in calls}
    assert "method" in {c.callee for c in calls}  # the inner call is still a real call site


def test_extract_calls_returns_none_for_unsupported_extension() -> None:
    """Same contract as parser.extract: an unsupported file is None, not an empty list."""
    assert extract_calls("x()", "notes.md") is None


def test_enclosing_entity_index_prefers_the_innermost_entity() -> None:
    """A method's call site is contained by both its class and itself; the method wins."""
    entities = [_entity("Service", 1, 20, kind="class"), _entity("run", 5, 10, kind="method")]
    assert enclosing_entity_index(entities, 6) == 1
    assert enclosing_entity_index(entities, 15) == 0


def test_enclosing_entity_index_is_none_at_module_level() -> None:
    """A call outside every entity has no caller to attribute it to."""
    assert enclosing_entity_index([_entity("run", 5, 10)], 2) is None


def test_resolve_calls_same_file_beats_everything() -> None:
    """A name defined in the calling file resolves there, at the highest confidence."""
    edges = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="helper", line=6, receiver=None)]},
        file_entities={
            "a.py": [_entity("caller", 5, 8), _entity("helper", 10, 12)],
            "b.py": [_entity("helper", 1, 3)],
        },
        file_imports={"a.py": {"b.py"}},
    )
    assert len(edges) == 1
    assert (edges[0].to_path, edges[0].to_entity_index) == ("a.py", 1)
    assert edges[0].confidence == CONFIDENCE_SAME_FILE
    assert edges[0].line == 6


def test_resolve_calls_falls_back_to_an_imported_file() -> None:
    """Not defined here but defined in exactly one imported file → the 0.85 tier."""
    edges = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="helper", line=6, receiver=None)]},
        file_entities={"a.py": [_entity("caller", 5, 8)], "b.py": [_entity("helper", 1, 3)]},
        file_imports={"a.py": {"b.py"}},
    )
    assert [(e.to_path, e.confidence) for e in edges] == [("b.py", CONFIDENCE_IMPORTED_FILE)]


def test_resolve_calls_falls_back_to_a_unique_repo_wide_name() -> None:
    """A bare call to a name defined exactly once anywhere resolves at 0.7."""
    edges = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="helper", line=6, receiver=None)]},
        file_entities={"a.py": [_entity("caller", 5, 8)], "c.py": [_entity("helper", 1, 3)]},
        file_imports={"a.py": set()},
    )
    assert [(e.to_path, e.confidence) for e in edges] == [("c.py", CONFIDENCE_UNIQUE_REPO_WIDE)]


def test_resolve_calls_drops_an_ambiguous_repo_wide_name() -> None:
    """Two definitions of the same name repo-wide resolve to nothing — no row per candidate."""
    edges = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="helper", line=6, receiver=None)]},
        file_entities={
            "a.py": [_entity("caller", 5, 8)],
            "b.py": [_entity("helper", 1, 3)],
            "c.py": [_entity("helper", 1, 3)],
        },
        file_imports={"a.py": set()},
    )
    assert edges == []


def test_resolve_calls_denies_qualified_calls_the_repo_wide_tier() -> None:
    """`x.foo()` stops at the imported-file tier — `x` could be any object at all."""
    entities = {"a.py": [_entity("caller", 5, 8)], "c.py": [_entity("finalize", 1, 3)]}
    qualified = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="finalize", line=6, receiver="util")]},
        file_entities=entities,
        file_imports={"a.py": set()},
    )
    bare = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="finalize", line=6, receiver=None)]},
        file_entities=entities,
        file_imports={"a.py": set()},
    )
    assert qualified == []
    assert len(bare) == 1


def test_resolve_calls_keeps_a_qualified_call_into_an_imported_file() -> None:
    """`util.finalize()` where util.ts is imported resolves — the zod-shaped case."""
    edges = resolve_calls(
        file_calls={"parse.ts": [ExtractedCall(callee="finalize", line=23, receiver="util")]},
        file_entities={
            "parse.ts": [_entity("_parse", 16, 30)],
            "util.ts": [_entity("finalize", 849, 900)],
        },
        file_imports={"parse.ts": {"util.ts"}},
    )
    assert [(e.to_path, e.line, e.confidence) for e in edges] == [
        ("util.ts", 23, CONFIDENCE_IMPORTED_FILE)
    ]


def test_resolve_calls_stops_self_calls_at_the_current_file() -> None:
    """`self.foo()` not defined here is inherited; name matching cannot follow that."""
    edges = resolve_calls(
        file_calls={"a.py": [ExtractedCall(callee="send", line=6, receiver="self")]},
        file_entities={"a.py": [_entity("caller", 5, 8)], "b.py": [_entity("send", 1, 3)]},
        file_imports={"a.py": {"b.py"}},
    )
    assert edges == []


def test_resolve_calls_drops_recursion_and_module_level_calls() -> None:
    """An entity calling itself carries no navigation, and a module-level call has no caller."""
    edges = resolve_calls(
        file_calls={
            "a.py": [
                ExtractedCall(callee="caller", line=6, receiver=None),  # recursive
                ExtractedCall(callee="caller", line=1, receiver=None),  # module level
            ]
        },
        file_entities={"a.py": [_entity("caller", 5, 8)]},
        file_imports={"a.py": set()},
    )
    assert edges == []


def test_extract_and_resolve_agree_on_the_same_file() -> None:
    """End to end on one file: parser entities + call walk produce the expected edge."""
    parsed = extract(PY_SOURCE, "svc.py")
    assert parsed is not None
    calls = extract_calls(PY_SOURCE, "svc.py")
    assert calls is not None
    edges = resolve_calls(
        file_calls={"svc.py": calls},
        file_entities={"svc.py": parsed.entities},
        file_imports={"svc.py": set()},
    )
    names = {
        (parsed.entities[e.from_entity_index].name, parsed.entities[e.to_entity_index].name)
        for e in edges
    }
    assert ("run", "step") in names
