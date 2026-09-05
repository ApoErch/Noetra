"""The repo map: a names-only table of contents, ranked by import-graph centrality.

The agent's weakest moment is its first tool call, made with no sense of the codebase. A few
hundred tokens of `path: Symbol, Symbol, …` lines — central files first — turns that blind
guess into an informed one (aider's idea). Built from `code_entity` + `dependency_edge`, no AI
calls, and cached per repository until it is re-indexed.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import tiktoken
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import CodeEntity, DependencyEdge, EntityKind, File, Repository

DAMPING = 0.85
ITERATIONS = 30
_encoding = tiktoken.get_encoding("o200k_base")


@dataclass
class FileSymbols:
    """One file and its top-level symbol names (classes and functions; methods are omitted to save tokens)."""

    path: str
    symbols: list[str] = field(default_factory=list)


def pagerank(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, float]:
    """PageRank by power iteration: a node is important if important nodes point at it.

    Each node starts with equal score; every round each node hands its score out evenly
    along its outgoing edges (plus a small `1 - DAMPING` share to everyone, so the scores
    never get stuck). Thirty rounds converge on graphs this sparse.
    """
    if not nodes:
        return {}
    out: dict[str, list[str]] = {node: [] for node in nodes}
    for src, dst in edges:
        if src in out and dst in out and src != dst:
            out[src].append(dst)
    n = len(nodes)
    rank = {node: 1.0 / n for node in nodes}
    for _ in range(ITERATIONS):
        # Nodes with no outgoing edges share their score with everyone (standard dangling fix).
        dangling = sum(rank[node] for node in nodes if not out[node])
        base = (1.0 - DAMPING) / n + DAMPING * dangling / n
        new = {node: base for node in nodes}
        for node in nodes:
            share = DAMPING * rank[node] / len(out[node]) if out[node] else 0.0
            for dst in out[node]:
                new[dst] += share
        rank = new
    return rank


def render_repo_map(files: list[FileSymbols], ranks: dict[str, float], token_budget: int) -> str:
    """Render `path: A, B, C` lines in rank order, stopping before the token budget is exceeded."""
    ordered = sorted(files, key=lambda f: (-ranks.get(f.path, 0.0), f.path))
    lines: list[str] = []
    used = 0
    for entry in ordered:
        line = f"{entry.path}: {', '.join(entry.symbols)}" if entry.symbols else entry.path
        cost = len(_encoding.encode(line)) + 1
        if used + cost > token_budget:
            break
        lines.append(line)
        used += cost
    omitted = len(ordered) - len(lines)
    if omitted:
        lines.append(f"… and {omitted} more files (use code_search to find them)")
    return "\n".join(lines)


def build_repo_map(db: Session, repository_id: uuid.UUID, token_budget: int) -> str:
    """Load parsed files, their top-level symbols and import edges, and render the ranked map."""
    files = db.execute(
        select(File.id, File.path).where(File.repository_id == repository_id, File.language.isnot(None))
    ).all()
    path_by_id = {file_id: path for file_id, path in files}
    if not path_by_id:
        return ""

    symbols_by_path: dict[str, list[str]] = {path: [] for path in path_by_id.values()}
    entities = db.execute(
        select(CodeEntity.file_id, CodeEntity.name)
        .where(CodeEntity.repository_id == repository_id, CodeEntity.kind != EntityKind.METHOD)
        .order_by(CodeEntity.start_line)
    ).all()
    for file_id, name in entities:
        path = path_by_id.get(file_id)
        if path is not None:
            symbols_by_path[path].append(name)

    edge_rows = db.execute(
        select(DependencyEdge.from_file_id, DependencyEdge.to_file_id).where(
            DependencyEdge.repository_id == repository_id
        )
    ).all()
    edges = [
        (path_by_id[src], path_by_id[dst])
        for src, dst in edge_rows
        if src in path_by_id and dst in path_by_id
    ]
    ranks = pagerank(list(path_by_id.values()), edges)
    entries = [FileSymbols(path=path, symbols=names) for path, names in symbols_by_path.items()]
    return render_repo_map(entries, ranks, token_budget)


# Keyed by repository id, invalidated when the repo row's updated_at changes (re-index).
_cache: dict[uuid.UUID, tuple[datetime, str]] = {}


def get_repo_map(db: Session, repo: Repository, token_budget: int) -> str:
    """Cached `build_repo_map` — a few ms to build, but it runs on every chat turn."""
    cached = _cache.get(repo.id)
    if cached is not None and cached[0] == repo.updated_at:
        return cached[1]
    rendered = build_repo_map(db, repo.id, token_budget)
    _cache[repo.id] = (repo.updated_at, rendered)
    return rendered
