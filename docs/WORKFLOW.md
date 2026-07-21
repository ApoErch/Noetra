# Workflow

## User journey

```
Login (GitHub OAuth)
      │
      ▼
Dashboard ── my repositories / recent analyses
      │
      ▼
Import repository ── public URL │ private (OAuth)
      │
      ▼
Background indexing  (async — user sees live status)
      │
      ▼
Repository ready
      │
      ▼
Repository dashboard
   ├── File tree browser (unlocks right after cloning — no wait on parsing/embedding)
   ├── AI Chat (LangGraph agent)
   ├── Search (hybrid)
   └── Metrics (basic)
```

1. **Login** — GitHub OAuth; fetch the user's repo list via the GitHub API.
2. **Import** — user picks a repo. API creates `Repository`
   (`status=queued`) and enqueues a Celery job. Response is immediate.
3. **Indexing** — runs in the worker (below); UI shows progress by stage.
4. **Explore** — the file tree browser unlocks as soon as `status` passes `cloning`
   (it only needs `file.path`/`content`, not the symbol table). Chat, search, and
   metrics stay gated until `status=ready`.

## Indexing pipeline (the core of the product)

A Celery job in `worker` with ordered stages. Each stage updates repository status +
progress so the UI reflects it live.

```
queued
  │
  ▼
cloning        git clone into worker storage
  │
  ▼
parsing        walk every py/js/ts file; Tree-sitter per language
  │            extract: files, functions, classes, methods, imports  → SYMBOL TABLE
  ▼
chunking       AST-aware chunks (by function/class, never fixed windows)
  │
  ▼
embedding      embed each chunk (embedding provider TBD) → store vector in pgvector
  │
  ▼
graphing       resolve imports → dependency edges between files
  │            (data only in V1 — no visualization until V2)
  ▼
metrics        basic aggregates: file count, function count, total LOC,
  │            language breakdown, largest files
  ▼
ready          everything persisted; chat + search + dashboard unlock
```

On any failure: `status=failed`, store the error, surface a retry action.

## Stage responsibilities

- **cloning** — `core/github`. Shallow clone where possible. Also enumerates every
  tracked file (`git ls-files` — respects `.gitignore`) and writes a `file` row per
  path with its raw `content` (or `is_binary=true` with no content), powering the
  file tree browser independent of parsing.
- **parsing** — `indexer`. One extractor per language (py/js/ts) via Tree-sitter.
  Produces plain structured data → files + `code_entity` rows (the symbol table).
- **chunking** — `indexer`. Chunk by AST node; each chunk keeps file, line range, and
  owning entity. See `RETRIEVAL.md` — this rule is non-negotiable.
- **embedding** — `core/ai`. Batched to control cost. Cache by file `content_hash` so
  re-indexing unchanged files is cheap.
- **graphing** — `indexer`. Resolve each import to a target file; write edges. Powers
  `list_dependencies` for the agent now, and the V2 architecture view later.
- **metrics** — pure aggregation over already-extracted data; cheap to recompute.
  V1 keeps this basic — counts, languages, largest files. No dead-code/complexity/etc.

## Re-indexing

Re-importing or refreshing re-runs the pipeline. Skip unchanged files by `content_hash`;
only changed files are re-chunked and re-embedded — saves time and embedding cost.
