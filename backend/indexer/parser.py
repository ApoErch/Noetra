"""Tree-sitter based extraction of functions, classes, methods, and imports from source files.

Pure functions only — no DB, no HTTP (see module boundaries in CLAUDE.md). Takes a file's
raw text in, returns plain structured data out, so this stays unit-testable without a
running Postgres. The `parsing` worker stage (worker/tasks.py) is what calls this and
persists the result as `code_entity` rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

# Extension -> the value stored in `file.language` (core/models.py). .jsx/.tsx map to the
# same "javascript"/"typescript" values as .js/.ts — they're the same language with JSX
# mixed in, not a distinct language (see CLAUDE.md scope note).
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

# Extension -> compiled Tree-sitter grammar. .tsx needs its own grammar, distinct from
# .ts's — TS generics (`<T>`) and JSX tags are ambiguous in a couple of spots, which is
# why tree-sitter-typescript ships two separate languages instead of one.
_PARSERS: dict[str, Parser] = {
    ".py": Parser(Language(tspython.language())),
    ".js": Parser(Language(tsjavascript.language())),
    ".jsx": Parser(Language(tsjavascript.language())),
    ".ts": Parser(Language(tstypescript.language_typescript())),
    ".tsx": Parser(Language(tstypescript.language_tsx())),
}

# Tree-sitter node type -> our entity kind, per language. Method vs. function is decided
# by walk-time context (nested inside a class body), not a distinct node type in Python —
# JS/TS do give methods their own node type (`method_definition`).
_FUNCTION_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition"},
    "typescript": {"function_declaration", "method_definition"},
}
_CLASS_NODE_TYPES: dict[str, set[str]] = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {"class_declaration"},
}
_IMPORT_NODE_TYPES: dict[str, set[str]] = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
}

# Arrow functions and anonymous `function` expressions have no name of their own —
# the name lives one level up, on whatever they're assigned to. These are the two
# assignment shapes we recognize: `const foo = () => {}` (variable_declarator) and
# a class property like `handleClick = () => {}` (field_definition). Only
# javascript/typescript have this pattern — Python has no anonymous-function-as-
# named-binding equivalent worth extracting (a `lambda` bound to a name is rare
# and, unlike JS arrow functions, never how Python code defines its real methods).
_ARROW_CONTAINER_NODE_TYPES: dict[str, set[str]] = {
    "python": set(),
    "javascript": {"variable_declarator", "field_definition"},
    "typescript": {"variable_declarator", "field_definition"},
}
_ARROW_VALUE_NODE_TYPES = {"arrow_function", "function_expression"}


@dataclass(frozen=True)
class ExtractedEntity:
    """One function/class/method found in a file, shaped to become a `code_entity` row."""

    kind: str  # "function" | "class" | "method"
    name: str
    signature: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ExtractedImport:
    """One imported module, as written in source — e.g. `.utils`, `..pkg.mod`, `pathlib`, `./foo`, `react`.

    This is still just syntax (Tree-sitter's own field access, no reasoning about what it
    points to) — resolving it to an actual target file in this repo happens later, in the
    graphing stage (indexer/graph.py), since that needs the whole repo's file list, not
    just one file's parse tree.
    """

    module: str
    start_line: int


@dataclass(frozen=True)
class ExtractionResult:
    """Everything pulled out of one file: its symbol-table entities and its raw imports."""

    entities: list[ExtractedEntity]
    imports: list[ExtractedImport]


def detect_language(path: str) -> str | None:
    """Map a repo-relative file path to a supported `file.language` value, or None if unsupported."""
    return _EXTENSION_LANGUAGES.get(PurePosixPath(path).suffix)


def extract(content: str, path: str) -> ExtractionResult | None:
    """Parse one file's source and pull out its functions, classes, methods, and imports.

    Returns None if `path`'s extension isn't one of the supported py/js/jsx/ts/tsx types.
    """
    extension = PurePosixPath(path).suffix
    language = _EXTENSION_LANGUAGES.get(extension)
    if language is None:
        return None

    source = content.encode("utf-8")
    tree = _PARSERS[extension].parse(source)

    entities: list[ExtractedEntity] = []
    imports: list[ExtractedImport] = []

    def walk(node: Node, inside_class: bool) -> None:
        node_type = node.type

        if node_type in _CLASS_NODE_TYPES[language]:
            entities.append(_make_entity(node, source, kind="class"))
            for child in node.named_children:
                walk(child, inside_class=True)
            return

        if node_type in _FUNCTION_NODE_TYPES[language]:
            kind = "method" if inside_class or node_type == "method_definition" else "function"
            entities.append(_make_entity(node, source, kind=kind))
            # Deliberately not descending into a function's own body — nested
            # helper defs/closures aren't part of the symbol table in V1.
            return

        if node_type in _ARROW_CONTAINER_NODE_TYPES[language]:
            value_node = node.child_by_field_name("value")
            if value_node is not None and value_node.type in _ARROW_VALUE_NODE_TYPES:
                # field_definition is only ever a class member; variable_declarator
                # is a method only when it's nested inside a class body itself
                # (uncommon, but a plain top-level `const` isn't a method).
                kind = "method" if (node_type == "field_definition" or inside_class) else "function"
                name_field = "property" if node_type == "field_definition" else "name"
                entities.append(_make_entity(node, source, kind=kind, name_field=name_field))
                return
            # A variable_declarator/field_definition whose value isn't a function
            # (e.g. `const x = 5`) — fall through to the generic recursion below,
            # since it can still contain other entities worth finding.

        if node_type in _IMPORT_NODE_TYPES[language]:
            imports.extend(_make_imports(node, source, language))
            return

        for child in node.named_children:
            walk(child, inside_class=inside_class)

    walk(tree.root_node, inside_class=False)
    return ExtractionResult(entities=entities, imports=imports)


def _make_entity(node: Node, source: bytes, kind: str, name_field: str = "name") -> ExtractedEntity:
    """Build an ExtractedEntity from a function/class/method node, reading its name and header via Tree-sitter's own field access."""
    name_node = node.child_by_field_name(name_field)
    name = source[name_node.start_byte : name_node.end_byte].decode("utf-8") if name_node else "<anonymous>"

    # The signature is everything from the node's start up to its body — i.e.
    # the `def foo(...):` / `class Foo:` header line(s), without re-deriving it
    # via string splitting. `variable_declarator`/`field_definition` (arrow
    # functions bound to a name) have no `body` field of their own — the body
    # lives one level down, on their `value` (the arrow_function/function_expression) — so fall
    # through to that before giving up and using the whole node.
    body_node = node.child_by_field_name("body")
    if body_node is None:
        value_node = node.child_by_field_name("value")
        if value_node is not None:
            body_node = value_node.child_by_field_name("body")
    header_end = body_node.start_byte if body_node else node.end_byte
    signature = source[node.start_byte : header_end].decode("utf-8").rstrip().rstrip(":").strip()

    return ExtractedEntity(
        kind=kind,
        name=name,
        signature=signature,
        start_line=node.start_point[0] + 1,  # Tree-sitter rows are 0-indexed; citations are 1-indexed
        end_line=node.end_point[0] + 1,
    )


def _node_text(node: Node, source: bytes) -> str:
    """Decode a node's exact source slice."""
    return source[node.start_byte : node.end_byte].decode("utf-8")


def _make_imports(node: Node, source: bytes, language: str) -> list[ExtractedImport]:
    """Build the ExtractedImport(s) named by one import node (an import/import_from statement)."""
    start_line = node.start_point[0] + 1

    if language == "python":
        if node.type == "import_from_statement":
            # One module regardless of how many names are imported from it
            # (`from x import a, b` is still one edge to `x`) — field access
            # gives us the module directly (a dotted_name or a relative_import
            # for leading-dot imports), whether it's absolute or relative.
            module_node = node.child_by_field_name("module_name")
            if module_node is None:
                return []
            return [ExtractedImport(module=_node_text(module_node, source), start_line=start_line)]

        # Plain `import_statement`: one or more comma-separated modules, each
        # either a bare `dotted_name` or an `aliased_import` (`... as x`) whose
        # real module name lives on its `name` field, not its alias.
        modules: list[str] = []
        for child in node.named_children:
            if child.type == "dotted_name":
                modules.append(_node_text(child, source))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    modules.append(_node_text(name_node, source))
        return [ExtractedImport(module=m, start_line=start_line) for m in modules]

    # javascript/typescript: every import_statement shape (default, named,
    # namespace, side-effect-only) has a `source` field — the quoted specifier.
    source_node = node.child_by_field_name("source")
    if source_node is None:
        return []
    return [ExtractedImport(module=_node_text(source_node, source).strip("\"'"), start_line=start_line)]
