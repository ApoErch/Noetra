"""Resolve extracted import module strings to actual files within the same repo — the graphing stage.

Pure functions only — no DB, no HTTP (see module boundaries in CLAUDE.md). Takes the file
paths a repo actually has plus each file's imports (from `indexer.parser.extract`) and
returns which imports resolve to which files. Bare/third-party specifiers (`import os`,
`from "react"`) resolve to nothing and are silently dropped: there's no stdlib/node_modules
indexed to resolve them against, and V1 only models edges within the repo itself.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

_JS_MODULE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
# Extensions a specifier may carry that name the *compiled output* rather than a file on
# disk. TypeScript's NodeNext resolution requires importing "./util.js" from util.ts, so
# these get stripped and retried against _JS_MODULE_EXTENSIONS above.
_JS_OUTPUT_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True)
class DependencyEdge:
    """One resolved import: `from_path` imports `to_path`, both repo-relative paths."""

    from_path: str
    to_path: str


def build_suffix_index(known_paths: set[str]) -> dict[str, set[str]]:
    """Map every path *suffix* (each directory component onward, including the whole path) to the full path(s) sharing it.

    Built once per repo so absolute-import resolution below is a single dict
    lookup instead of a linear scan over every file — the scan approach took
    ~8 minutes on tensorflow/tensorflow (36k files, thousands of absolute
    imports: O(imports * files) is only cheap at small repo scale).
    """
    index: dict[str, set[str]] = {}
    for path in known_paths:
        parts = path.split("/")
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            index.setdefault(suffix, set()).add(path)
    return index


def resolve_python_import(
    from_path: str, module: str, known_paths: set[str], suffix_index: dict[str, set[str]]
) -> str | None:
    """Resolve one Python import's module string to a repo-relative file path, or None if it's not in this repo (stdlib/third-party)."""
    leading_dots = len(module) - len(module.lstrip("."))
    remainder = module[leading_dots:]  # e.g. "utils.helpers", or "" for a bare "."/".."

    if leading_dots == 0:
        # Absolute import: we don't know the repo's real import root (repo root?
        # a `src/` layout? something else?), so match by suffix against every
        # known file — via the precomputed index, not a fresh scan per import.
        candidate = remainder.replace(".", "/")
        matches = suffix_index.get(f"{candidate}.py", set()) | suffix_index.get(f"{candidate}/__init__.py", set())
        return next(iter(matches)) if len(matches) == 1 else None

    # Relative import: one leading dot means "this file's own package" (i.e.
    # its parent directory) — each additional dot walks up one more level from
    # there. This resolves to an exact path, unlike the absolute case above.
    base_dir = posixpath.dirname(from_path)
    for _ in range(leading_dots - 1):
        base_dir = posixpath.dirname(base_dir)

    if not remainder:
        # Bare dots ("from . import x") name the package itself, not a
        # specific submodule — point at that package's __init__.
        init_candidate = posixpath.normpath(posixpath.join(base_dir, "__init__.py"))
        return init_candidate if init_candidate in known_paths else None

    candidate = posixpath.normpath(posixpath.join(base_dir, remainder.replace(".", "/")))
    if f"{candidate}.py" in known_paths:
        return f"{candidate}.py"
    init_candidate = posixpath.normpath(posixpath.join(candidate, "__init__.py"))
    return init_candidate if init_candidate in known_paths else None


def resolve_js_import(from_path: str, specifier: str, known_paths: set[str]) -> str | None:
    """Resolve one JS/TS import specifier to a repo-relative file path, or None (bare specifiers like "react" are npm packages, not in this repo)."""
    if not (specifier.startswith("./") or specifier.startswith("../")):
        return None

    base_dir = posixpath.dirname(from_path)
    candidate = posixpath.normpath(posixpath.join(base_dir, specifier))

    if candidate in known_paths:
        return candidate

    # TypeScript under `moduleResolution: NodeNext` imports the *emitted* path, so source
    # that lives in `util.ts` is imported as `"./util.js"`. Taking the specifier literally
    # means looking for `util.js`, then `util.js.ts`, and resolving nothing — which is why
    # zod produced 3 import edges for 1,400 entities. Strip a JS output extension and retry
    # against the source extensions.
    stems = [candidate]
    stem, extension = posixpath.splitext(candidate)
    if extension in _JS_OUTPUT_EXTENSIONS:
        stems.append(stem)

    for stem_candidate in stems:
        for ext in _JS_MODULE_EXTENSIONS:
            if f"{stem_candidate}{ext}" in known_paths:
                return f"{stem_candidate}{ext}"
    for stem_candidate in stems:
        for ext in _JS_MODULE_EXTENSIONS:
            index_candidate = posixpath.join(stem_candidate, f"index{ext}")
            if index_candidate in known_paths:
                return index_candidate
    return None


def resolve_dependencies(
    file_imports: dict[str, tuple[str, list[str]]], known_paths: set[str]
) -> list[DependencyEdge]:
    """Resolve every file's imports to edges. `file_imports` maps each file path to (language, [module strings])."""
    suffix_index = build_suffix_index(known_paths)
    edges: set[DependencyEdge] = set()
    for from_path, (language, modules) in file_imports.items():
        for module in modules:
            if language == "python":
                to_path = resolve_python_import(from_path, module, known_paths, suffix_index)
            else:
                to_path = resolve_js_import(from_path, module, known_paths)
            if to_path is not None and to_path != from_path:
                edges.add(DependencyEdge(from_path=from_path, to_path=to_path))
    return list(edges)
