"""AST-aware chunking: split a parsed file into retrieval units that respect syntax boundaries.

Pure functions only — no DB, no HTTP (see module boundaries in CLAUDE.md). Takes a file's
text plus the entities `indexer.parser.extract` already found, returns plain data out. The
`chunking` worker stage (worker/indexing.py) is what calls this and persists the result.

A chunk that splits a function in half poisons retrieval (docs/RETRIEVAL.md, "chunking
rule"), so entity chunks are never split regardless of length. Only the gaps between
entities — imports, module constants, class headers, or a whole file with no entities at
all (markdown, JSON, config) — get split, and those have no syntax boundary to damage.
"""

from __future__ import annotations

from dataclasses import dataclass

from indexer.parser import ExtractedEntity

MAX_GAP_LINES = 80

# Separates the parts of a chunk's context prefix ("path › class › signature"). A character
# that essentially never occurs in source, so it can't be confused with the code itself.
_PREFIX_SEPARATOR = " › "


@dataclass(frozen=True)
class Chunk:
    """One retrieval unit: a line range of a file, its source, and the context prefix describing it."""

    start_line: int  # 1-indexed, inclusive
    end_line: int  # inclusive
    content: str  # the raw source of those lines, as returned to the model
    embed_text: str  # the context prefix + content — what gets indexed, and later embedded
    entity_index: int | None  # position in the `entities` argument, or None for a gap chunk


def chunk_file(content: str, path: str, entities: list[ExtractedEntity]) -> list[Chunk]:
    """Split one file into chunks: one per leaf entity, plus gap chunks covering everything else.

    A "leaf" entity is one containing no other entity — so a class yields chunks for its
    methods, not a second copy of the whole class body. The lines a leaf doesn't cover
    (imports, the class header itself, module-level code) come back as gap chunks, so every
    line of the file stays retrievable.
    """
    lines = content.splitlines()
    if not lines:
        return []

    order = sorted(range(len(entities)), key=lambda i: (entities[i].start_line, -entities[i].end_line))
    leaves = _leaf_chunks(lines, path, entities, order)

    chunks = leaves + _gap_chunks(lines, path, [(c.start_line, c.end_line) for c in leaves])
    return sorted(chunks, key=lambda c: c.start_line)


def _leaf_chunks(
    lines: list[str], path: str, entities: list[ExtractedEntity], order: list[int]
) -> list[Chunk]:
    """Build one chunk per entity that contains no other entity, prefixed with its enclosing class."""
    chunks: list[Chunk] = []
    open_containers: list[int] = []  # indices of entities we're currently nested inside

    for position, index in enumerate(order):
        entity = entities[index]
        # Sorted by start line, so anything ending before this entity starts can't contain it.
        while open_containers and entities[open_containers[-1]].end_line < entity.start_line:
            open_containers.pop()

        # The next entity in start order beginning inside this one means this one is a
        # container (a class holding methods), not a leaf — its body is covered by its children.
        next_index = order[position + 1] if position + 1 < len(order) else None
        is_leaf = next_index is None or entities[next_index].start_line > entity.end_line

        if is_leaf:
            enclosing = next(
                (entities[i].name for i in reversed(open_containers) if entities[i].kind == "class"),
                None,
            )
            chunks.append(
                _make_chunk(
                    lines,
                    path,
                    start_line=entity.start_line,
                    end_line=entity.end_line,
                    entity_index=index,
                    enclosing_class=enclosing,
                    signature=entity.signature,
                )
            )
        open_containers.append(index)

    return chunks


def _gap_chunks(lines: list[str], path: str, covered: list[tuple[int, int]]) -> list[Chunk]:
    """Chunk every line range no entity chunk covers, split at MAX_GAP_LINES and skipping blank runs."""
    chunks: list[Chunk] = []
    cursor = 1  # next uncovered line, 1-indexed

    for start, end in sorted(covered):
        if start > cursor:
            chunks.extend(_split_gap(lines, path, cursor, start - 1))
        cursor = max(cursor, end + 1)

    if cursor <= len(lines):
        chunks.extend(_split_gap(lines, path, cursor, len(lines)))
    return chunks


def _split_gap(lines: list[str], path: str, start_line: int, end_line: int) -> list[Chunk]:
    """Cut one uncovered range into MAX_GAP_LINES-sized chunks, dropping any that are entirely blank."""
    chunks: list[Chunk] = []
    for chunk_start in range(start_line, end_line + 1, MAX_GAP_LINES):
        chunk_end = min(chunk_start + MAX_GAP_LINES - 1, end_line)
        if not any(line.strip() for line in lines[chunk_start - 1 : chunk_end]):
            continue  # a run of blank lines carries nothing to retrieve
        chunks.append(
            _make_chunk(
                lines,
                path,
                start_line=chunk_start,
                end_line=chunk_end,
                entity_index=None,
                enclosing_class=None,
                signature=None,
            )
        )
    return chunks


def _make_chunk(
    lines: list[str],
    path: str,
    *,
    start_line: int,
    end_line: int,
    entity_index: int | None,
    enclosing_class: str | None,
    signature: str | None,
) -> Chunk:
    """Assemble a Chunk, building the `path › class › signature` context prefix that heads its embed_text.

    A bare function body is ambiguous — `def refresh(self, token)` could be a cache, a
    session, or OAuth. The prefix says which, which is why it (not the raw source) is what
    gets indexed and embedded (M6). This pattern is called contextual retrieval.
    """
    body = "\n".join(lines[start_line - 1 : end_line])
    prefix = _PREFIX_SEPARATOR.join(part for part in (path, enclosing_class, signature) if part)
    return Chunk(
        start_line=start_line,
        end_line=end_line,
        content=body,
        embed_text=f"{prefix}\n{body}",
        entity_index=entity_index,
    )
