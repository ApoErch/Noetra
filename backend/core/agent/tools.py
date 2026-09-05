"""The agent's four tools: `code_search`, `read_file`, `list_dependencies`, `find_references`.

Each tool is a LangChain `@tool` whose LLM-facing arguments are the schema the model sees;
`db` and `repository_id` are `InjectedToolArg`s — hidden from the model, supplied by the
graph's tools node per request. Every result line carries `path:start-end`, because that is
where the agent's citations come from (docs/RETRIEVAL.md: citations come from tool-result
metadata, never from the model).
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, InjectedToolArg, tool
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from core.models import Chunk, CodeEntity, DependencyEdge, File, ReferenceEdge
from core.retrieval import RetrievalHit, search

# Fewer, better hits per search — the agent can search again with different terms, and every
# junk line it reads is context it has to carry (SWE-grep's precision-over-recall finding).
SEARCH_LIMIT = 10
# A whole-file read of a 1,200-line module is the single biggest context-pollution source;
# the tool truncates and tells the model where to continue instead.
READ_MAX_LINES = 200
# Lines are not a safe unit on their own — a minified bundle or an inline SVG is one line of
# hundreds of thousands of characters (requests' logo.svg: 884k chars ≈ 220k tokens, over the
# model's per-request limit). Cap by characters too, and per snippet line.
READ_MAX_CHARS = 12_000
SNIPPET_MAX_CHARS = 160
# Reference lists are enumerations, so the cap is much higher than SEARCH_LIMIT: cutting
# "every caller of X" at 10 would reintroduce the exact incompleteness this tool exists to
# fix. Each row is one short line, so 30 is still far cheaper than a single file read.
REFERENCES_LIMIT = 30
# Edges below this are treated as guesses. indexer/calls.py writes nothing under 0.7.
MIN_REFERENCE_CONFIDENCE = 0.5


@dataclass
class ToolResult:
    """What one tool call produced: the text the model reads, the ranges it may now cite, and a UI status line."""

    text: str
    summary: str
    hits: list[RetrievalHit] = field(default_factory=list)


def call_key(name: str, args: dict[str, Any]) -> str:
    """Stable identity for a tool call, so the graph can detect the same call being repeated."""
    return f"{name}:{json.dumps(args, sort_keys=True)}"


def format_hits(hits: list[RetrievalHit]) -> str:
    """Render search hits as one line each: `path:start-end · snippet [sources]`."""
    lines = []
    for hit in hits:
        sources = ",".join(source.value for source in hit.sources)
        snippet = hit.snippet.strip()
        if len(snippet) > SNIPPET_MAX_CHARS:
            snippet = snippet[:SNIPPET_MAX_CHARS] + "…"
        lines.append(f"{hit.path}:{hit.start_line}-{hit.end_line} · {snippet} [{sources}]")
    return "\n".join(lines)


def format_numbered(content: str, start_line: int, end_line: int) -> str:
    """Render `content` lines `start_line..end_line` (1-indexed, inclusive) as `NN| code`."""
    lines = content.splitlines()
    width = len(str(end_line))
    return "\n".join(f"{n:>{width}}| {lines[n - 1]}" for n in range(start_line, end_line + 1))


def clamp_window(total_lines: int, start_line: int | None, end_line: int | None) -> tuple[int, int, bool]:
    """Resolve an optional line window against the file length and the read cap.

    Returns `(start, end, truncated)`; `truncated` means the requested (or implied) window was
    longer than READ_MAX_LINES and was cut short.
    """
    start = max(1, start_line or 1)
    end = min(total_lines, end_line or total_lines)
    truncated = end - start + 1 > READ_MAX_LINES
    if truncated:
        end = start + READ_MAX_LINES - 1
    return start, end, truncated


@tool
def code_search(
    query: str,
    db: Annotated[Session, InjectedToolArg],
    repository_id: Annotated[uuid.UUID, InjectedToolArg],
) -> ToolResult:
    """Search the repository's code and docs. Returns ranked locations as `path:start-end · snippet`.

    Use an exact identifier (function/class/variable name, error string, config key) when the
    question names one — that is the fastest route. Use a short natural-language phrase for
    conceptual questions whose words may not appear in the code. Results are ranked; if they
    look wrong, search again with different terms rather than adding more words to one query.
    """
    hits = search(db, repository_id, query, limit=SEARCH_LIMIT)
    if not hits:
        return ToolResult(
            text="0 hits. Try fewer words, an exact identifier, or a different phrasing.",
            summary=f'searched "{query}" — no hits',
        )
    return ToolResult(text=format_hits(hits), summary=f'searched "{query}" — {len(hits)} hits', hits=hits)


@tool
def read_file(
    path: str,
    db: Annotated[Session, InjectedToolArg],
    repository_id: Annotated[uuid.UUID, InjectedToolArg],
    start_line: int | None = None,
    end_line: int | None = None,
) -> ToolResult:
    """Read a range of a file as numbered lines. `path` is repo-relative, exactly as search returned it.

    Read only the range you need (e.g. the function a search hit pointed at); at most 200 lines
    are returned per call, so read a long file in pieces rather than all at once.
    """
    file = db.scalar(select(File).where(File.repository_id == repository_id, File.path == path))
    if file is None:
        suggestions = _similar_paths(db, repository_id, path)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return ToolResult(
            text=f"No such file: {path!r}. Paths are repo-relative, as returned by code_search.{hint}",
            summary=f"read {path} — not found",
        )
    if file.content is None:
        return ToolResult(text=f"{path} is binary or too large to display.", summary=f"read {path} — not text")

    lines = file.content.splitlines()
    total = len(lines)
    start, end, truncated = clamp_window(total, start_line, end_line)
    if total == 0 or start > total:
        return ToolResult(text=f"{path} has only {total} lines.", summary=f"read {path} — out of range")

    text = format_numbered(file.content, start, end)
    if len(text) > READ_MAX_CHARS:
        text = text[:READ_MAX_CHARS] + (
            f"\n… truncated at {READ_MAX_CHARS} characters (very long lines — this file is not readable in full)."
        )
    elif truncated:
        text += f"\n… truncated at {READ_MAX_LINES} lines; call read_file again from line {end + 1} to continue."
    hit = RetrievalHit(
        file_id=file.id,
        path=path,
        start_line=start,
        end_line=end,
        snippet=lines[start - 1] if lines else "",
        score=0.0,
        sources=[],
    )
    return ToolResult(text=text, summary=f"read {path}:{start}-{end}", hits=[hit])


@tool
def list_dependencies(
    path: str,
    db: Annotated[Session, InjectedToolArg],
    repository_id: Annotated[uuid.UUID, InjectedToolArg],
    direction: Literal["imports", "imported_by"] = "imports",
) -> ToolResult:
    """List a file's import relationships. `imports`: files this file imports. `imported_by`: files that import it.

    Use it to follow a module's dependencies or to find who uses a module, without reading
    every file. Only imports resolved inside this repository are listed (no stdlib/npm).
    """
    file = db.scalar(select(File).where(File.repository_id == repository_id, File.path == path))
    if file is None:
        return ToolResult(text=f"No such file: {path!r}.", summary=f"dependencies of {path} — not found")

    if direction == "imports":
        stmt = (
            select(File.path)
            .join(DependencyEdge, DependencyEdge.to_file_id == File.id)
            .where(DependencyEdge.from_file_id == file.id)
        )
    else:
        stmt = (
            select(File.path)
            .join(DependencyEdge, DependencyEdge.from_file_id == File.id)
            .where(DependencyEdge.to_file_id == file.id)
        )
    paths = sorted(db.scalars(stmt).all())
    label = "imports" if direction == "imports" else "is imported by"
    if not paths:
        return ToolResult(text=f"{path} {label} no files in this repository.", summary=f"{path} {label} nothing")
    return ToolResult(
        text=f"{path} {label}:\n" + "\n".join(paths),
        summary=f"{path} {label} {len(paths)} files",
    )


@tool
def find_references(
    symbol: str,
    db: Annotated[Session, InjectedToolArg],
    repository_id: Annotated[uuid.UUID, InjectedToolArg],
    direction: Literal["callers", "callees"] = "callers",
) -> ToolResult:
    """Walk the call graph from one function/class/method by name. `callers`: what calls it. `callees`: what it calls.

    Use it when the question is about *reach* rather than content — every place a function is
    used, what a function depends on, or what a change to it would affect. It returns the
    complete set, which code_search cannot: search ranks by relevance and returns at most two
    hits per file, so it cannot enumerate five call sites in one module. One hop per call —
    call it again on a result to go further.
    """
    name = symbol.strip().removesuffix("()").rsplit(".", 1)[-1]
    entity_ids = list(
        db.scalars(
            select(CodeEntity.id).where(
                CodeEntity.repository_id == repository_id, CodeEntity.name == name
            )
        ).all()
    )
    if not entity_ids:
        suggestions = _similar_names(db, repository_id, name)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return ToolResult(
            text=f"No function, class or method named {name!r} in this repository.{hint}",
            summary=f"references of {name} — unknown symbol",
        )

    Other = aliased(CodeEntity)
    OtherFile = aliased(File)
    # callers: the edges pointing AT this symbol, and the entity on the other end is the
    # caller. callees: the edges leaving it. Same row shape either way, so one formatter.
    near, far = (
        (ReferenceEdge.to_entity_id, ReferenceEdge.from_entity_id)
        if direction == "callers"
        else (ReferenceEdge.from_entity_id, ReferenceEdge.to_entity_id)
    )
    stmt = (
        select(
            Other.id,
            Other.name,
            Other.start_line,
            Other.end_line,
            OtherFile.id.label("file_id"),
            OtherFile.path,
            ReferenceEdge.line,
        )
        .join(ReferenceEdge, far == Other.id)
        .join(OtherFile, Other.file_id == OtherFile.id)
        .where(
            ReferenceEdge.repository_id == repository_id,
            near.in_(entity_ids),
            # Resolution tiers all sit at 0.7+ today, so this excludes nothing yet; it is the
            # contract that lets a future lower-confidence tier be added without every caller
            # suddenly inheriting its guesses.
            ReferenceEdge.confidence >= MIN_REFERENCE_CONFIDENCE,
        )
        .order_by(ReferenceEdge.confidence.desc(), OtherFile.path, ReferenceEdge.line)
    )
    rows = list(db.execute(stmt).all())
    relation = "calls" if direction == "callers" else "called by"
    if not rows:
        return ToolResult(
            text=(
                f"No {direction} of {name} found in this repository. The call graph resolves names "
                f"without type inference, so dynamic dispatch and inherited methods are missing "
                f"from it — code_search may still find uses."
            ),
            summary=f"{direction} of {name} — none",
        )

    shown = rows[:REFERENCES_LIMIT]
    # One chunk per leaf entity, so this is a lookup not a join fan-out. A container entity
    # (a class holding methods) has no chunk of its own; its own line range is just as real
    # a citation, so fall back to it rather than dropping the row.
    chunks = {
        chunk.entity_id: chunk
        for chunk in db.scalars(
            select(Chunk).where(Chunk.entity_id.in_([row.id for row in shown]))
        ).all()
    }

    lines: list[str] = []
    hits: list[RetrievalHit] = []
    for row in shown:
        chunk = chunks.get(row.id)
        start = chunk.start_line if chunk else row.start_line
        end = chunk.end_line if chunk else row.end_line
        lines.append(f"{row.path}:{start}-{end} · {row.name} — {relation} {name} at line {row.line}")
        hits.append(
            RetrievalHit(
                file_id=row.file_id,
                path=row.path,
                start_line=start,
                end_line=end,
                snippet=f"{row.name} — {relation} {name} at line {row.line}",
                score=0.0,
                sources=[],
                match_line=row.line,
            )
        )

    text = f"{name} — {direction}:\n" + "\n".join(lines)
    if len(rows) > REFERENCES_LIMIT:
        text += f"\n… {len(rows) - REFERENCES_LIMIT} more not shown."
    return ToolResult(text=text, summary=f"{direction} of {name} — {len(rows)} found", hits=hits)


def _similar_names(db: Session, repository_id: uuid.UUID, name: str, limit: int = 3) -> list[str]:
    """Up to `limit` entity names containing the requested one — the usual near-miss shape."""
    if not name:
        return []
    stmt = (
        select(CodeEntity.name)
        .where(CodeEntity.repository_id == repository_id, CodeEntity.name.ilike(f"%{name}%"))
        .distinct()
        .order_by(CodeEntity.name)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def _similar_paths(db: Session, repository_id: uuid.UUID, path: str, limit: int = 3) -> list[str]:
    """Up to `limit` repo paths ending with the requested path's basename — the usual wrong-guess shape."""
    basename = path.rsplit("/", 1)[-1]
    if not basename:
        return []
    stmt = (
        select(File.path)
        .where(File.repository_id == repository_id, File.path.like(f"%{basename}"))
        .order_by(File.path)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


# find_references last: it is the one that needs a location first, so its position in the
# list matches the order the agent naturally reaches for them.
TOOLS: list[BaseTool] = [code_search, read_file, list_dependencies, find_references]
# The graph tool alone, so eval/agent.py can run the M8 ablation (tools on vs. off)
# without hand-editing this module the way the M5 structural ablation had to.
GRAPH_TOOLS: list[BaseTool] = [find_references]
TOOLS_BY_NAME: dict[str, BaseTool] = {t.name: t for t in TOOLS}
