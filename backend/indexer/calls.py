"""Call-site extraction and name-based call-graph resolution — the `reference_edge` half of graphing.

Two passes, both pure (no DB, no HTTP — see CLAUDE.md module boundaries):

1. `extract_calls` walks a file *into* function bodies, which `parser.extract` deliberately
   does not (its walk stops at a function so nested closures stay out of the symbol table).
   It yields bare call sites — a name, a line, and what it was called on — with no attempt
   to say what they point at.
2. `resolve_calls` turns those names into entity-to-entity edges by matching them against
   the symbol table, in confidence tiers.

This is the standard "poor man's call graph" (ctags, aider): name matching, no type
inference. It is approximate on purpose, because its only job is to orient an agent that
then reads the real file.

**Precision over recall is the design rule.** A wrong edge sends the agent to code that has
nothing to do with the question and it answers from there; a missing edge just leaves it
searching, which it is already good at. So every tier requires exactly one candidate and an
ambiguous name resolves to nothing at all — no low-confidence row per candidate. That is
the ARISE (arXiv 2605.03117) finding: "spurious call edges lead agents down incorrect paths
and are more harmful than missing edges."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from indexer.parser import _EXTENSION_LANGUAGES, _PARSERS, ExtractedEntity

# Confidence per resolution tier. Tools filter at >= 0.5, so every tier we actually write
# is trusted; the tier still travels with the edge so a caller can prefer the certain ones.
CONFIDENCE_SAME_FILE = 0.9
CONFIDENCE_IMPORTED_FILE = 0.85
CONFIDENCE_UNIQUE_REPO_WIDE = 0.7

# Call nodes per language. Python spells instantiation as a plain call (`Thing()`), so
# JS/TS's `new_expression` is included to keep the two symmetric — a constructor call is a
# real reference to a class entity.
_CALL_NODE_TYPES: dict[str, set[str]] = {
    "python": {"call"},
    "javascript": {"call_expression", "new_expression"},
    "typescript": {"call_expression", "new_expression"},
}

# The field holding the thing being called, and the receiver/name fields of the qualified
# form — all verified against the live grammars rather than assumed:
#   python      call            -> function: identifier | attribute(object, attribute)
#   js/ts       call_expression -> function: identifier | member_expression(object, property)
#   js/ts       new_expression  -> constructor: identifier
_CALLEE_FIELDS = ("function", "constructor")
_QUALIFIED_NODE_TYPES = {"attribute", "member_expression"}
_NAME_FIELDS = ("attribute", "property")

# Receiver texts that mean "this object", so the call resolves against the current file.
_SELF_RECEIVERS = {"self", "this", "cls"}


@dataclass(frozen=True)
class ExtractedCall:
    """One call site as written: the bare name being called, its line, and its receiver.

    `receiver` is None for `foo()`, "self"/"this"/"cls" for a call on the current object,
    and the receiver's source text otherwise (`util` in `util.foo()`). Resolution treats
    those three cases differently, so the distinction has to survive extraction.
    """

    callee: str
    line: int
    receiver: str | None


@dataclass(frozen=True)
class ReferenceEdge:
    """One resolved call, addressed the way chunker addresses entities: by list position.

    `from_entity_index` / `to_entity_index` index into that file's `ExtractedEntity` list,
    because a pure indexer has no database ids to work with. The worker maps them onto real
    `CodeEntity.id`s the same way it already does for `chunk.entity_index`.
    """

    from_path: str
    from_entity_index: int
    to_path: str
    to_entity_index: int
    line: int
    confidence: float


def extract_calls(content: str, path: str) -> list[ExtractedCall] | None:
    """Find every call site in one file, descending all the way into function bodies.

    Returns None if `path`'s extension isn't one of the supported py/js/jsx/ts/tsx types.
    """
    extension = PurePosixPath(path).suffix
    language = _EXTENSION_LANGUAGES.get(extension)
    if language is None:
        return None

    source = content.encode("utf-8")
    tree = _PARSERS[extension].parse(source)
    call_node_types = _CALL_NODE_TYPES[language]
    calls: list[ExtractedCall] = []

    def walk(node: Node) -> None:
        if node.type in call_node_types:
            call = _make_call(node, source)
            if call is not None:
                calls.append(call)
            # Keep descending: arguments are very often calls themselves
            # (`f(g(x))`), and a chained call's receiver is a call too.
        for child in node.named_children:
            walk(child)

    walk(tree.root_node)
    return calls


def _make_call(node: Node, source: bytes) -> ExtractedCall | None:
    """Read one call node into an ExtractedCall, or None for a shape we deliberately don't resolve."""
    callee_node: Node | None = None
    for field in _CALLEE_FIELDS:
        callee_node = node.child_by_field_name(field)
        if callee_node is not None:
            break
    if callee_node is None:
        return None

    line = node.start_point[0] + 1

    if callee_node.type == "identifier":
        return ExtractedCall(callee=_text(callee_node, source), line=line, receiver=None)

    if callee_node.type in _QUALIFIED_NODE_TYPES:
        name_node: Node | None = None
        for field in _NAME_FIELDS:
            name_node = callee_node.child_by_field_name(field)
            if name_node is not None:
                break
        object_node = callee_node.child_by_field_name("object")
        if name_node is None or object_node is None:
            return None
        # A receiver that is itself an expression (`obj.method().chained()`,
        # `d["k"].f()`) is dynamic dispatch we cannot resolve by name, so it is
        # dropped rather than guessed at.
        if object_node.type not in {"identifier", "this", "attribute", "member_expression"}:
            return None
        return ExtractedCall(
            callee=_text(name_node, source),
            line=line,
            receiver=_text(object_node, source),
        )

    # Anything else being called — an inline arrow, a parenthesised expression,
    # a subscript — has no name to match against the symbol table.
    return None


def _text(node: Node, source: bytes) -> str:
    """Decode a node's exact source slice."""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def enclosing_entity_index(entities: list[ExtractedEntity], line: int) -> int | None:
    """Index of the innermost entity whose range contains `line`, or None if it sits at module level.

    Innermost matters because a method's call sites are contained by both the method and its
    class; the method is the useful caller. Same leaf-preferring rule the chunker uses.
    Module-level calls (a JS `const x = f()`, a Python module constant) have no enclosing
    entity and are dropped — `reference_edge.from_entity_id` is not nullable in V1.
    """
    best: int | None = None
    for index, entity in enumerate(entities):
        if entity.start_line <= line <= entity.end_line:
            if best is None or entity.start_line > entities[best].start_line:
                best = index
    return best


def resolve_calls(
    file_calls: dict[str, list[ExtractedCall]],
    file_entities: dict[str, list[ExtractedEntity]],
    file_imports: dict[str, set[str]],
) -> list[ReferenceEdge]:
    """Resolve every extracted call to an entity-to-entity edge, dropping whatever is ambiguous.

    `file_imports` maps a file path to the repo paths it imports — the already-resolved
    output of `graph.resolve_dependencies`, reused so the "a file this one imports" tier
    costs nothing extra.

    Tiers, strongest first: defined in the same file (0.9), defined in exactly one imported
    file (0.85), defined exactly once repo-wide (0.7). A tier that matches more than one
    entity resolves to nothing — it does not fall through to a weaker tier, because a name
    defined twice nearby is not better explained by a match further away.
    """
    by_file = {path: _name_index(entities) for path, entities in file_entities.items()}
    repo_wide = _repo_wide_index(file_entities)

    edges: set[ReferenceEdge] = set()
    for from_path, calls in file_calls.items():
        entities = file_entities.get(from_path, [])
        if not entities:
            continue
        imported = file_imports.get(from_path, set())
        for call in calls:
            from_index = enclosing_entity_index(entities, call.line)
            if from_index is None:
                continue
            target = _resolve_one(call, from_path, imported, by_file, repo_wide)
            if target is None:
                continue
            to_path, to_index, confidence = target
            # A recursive call carries no navigational information — the agent is
            # already looking at that entity.
            if to_path == from_path and to_index == from_index:
                continue
            edges.add(
                ReferenceEdge(
                    from_path=from_path,
                    from_entity_index=from_index,
                    to_path=to_path,
                    to_entity_index=to_index,
                    line=call.line,
                    confidence=confidence,
                )
            )
    return list(edges)


def _resolve_one(
    call: ExtractedCall,
    from_path: str,
    imported: set[str],
    by_file: dict[str, dict[str, list[int]]],
    repo_wide: dict[str, list[tuple[str, int]]],
) -> tuple[str, int, float] | None:
    """Apply the tier cascade to one call site; None when nothing resolves unambiguously."""
    same_file = by_file.get(from_path, {}).get(call.callee, [])
    if len(same_file) == 1:
        return from_path, same_file[0], CONFIDENCE_SAME_FILE
    if same_file:
        return None  # defined twice right here — ambiguous, and no distant match beats it

    # `self.foo()` names a member of the current object. Anything not found in this file
    # is inherited from a base class, which name matching cannot follow, so it stops here.
    if call.receiver in _SELF_RECEIVERS:
        return None

    candidates = [
        (path, index)
        for path in imported
        for index in by_file.get(path, {}).get(call.callee, [])
    ]
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1], CONFIDENCE_IMPORTED_FILE
    if candidates:
        return None

    # Repo-wide uniqueness is a fair fallback for a bare `foo()`, because the language's
    # own scoping rules mean the name had to be imported or defined to be callable. It is
    # NOT fair for `x.foo()`, where `x` could be any object at all and a same-named repo
    # function is a coincidence — so qualified calls stop at the imported-file tier.
    if call.receiver is not None:
        return None
    everywhere = repo_wide.get(call.callee, [])
    if len(everywhere) == 1:
        return everywhere[0][0], everywhere[0][1], CONFIDENCE_UNIQUE_REPO_WIDE
    return None


def _name_index(entities: list[ExtractedEntity]) -> dict[str, list[int]]:
    """Map each entity name in one file to the positions holding it."""
    index: dict[str, list[int]] = {}
    for position, entity in enumerate(entities):
        index.setdefault(entity.name, []).append(position)
    return index


def _repo_wide_index(
    file_entities: dict[str, list[ExtractedEntity]],
) -> dict[str, list[tuple[str, int]]]:
    """Map each entity name to every (path, position) defining it, across the whole repo.

    Built once per repo rather than scanned per call — the same discipline as
    `graph.build_suffix_index`, and for the same reason: a call graph runs an order of
    magnitude more lookups than import resolution, so a linear scan per call is what turned
    graphing into an eight-minute stage on a large repo once already.
    """
    index: dict[str, list[tuple[str, int]]] = {}
    for path, entities in file_entities.items():
        for position, entity in enumerate(entities):
            index.setdefault(entity.name, []).append((path, position))
    return index
