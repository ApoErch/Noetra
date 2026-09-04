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
4. **Explore** — surfaces unlock progressively as the pipeline advances, not all at once
   when it finishes:
   - after `cloning` — the **file tree browser**. Needs only `file.path` / `file.content`,
     which clone persists.
   - after `chunking` — **lexical search**. `search()` runs over `chunk.content_tsv`, so it
     needs chunks to exist; the `tsvector` is a generated column, so there's no separate
     index step once they do.
   - after `embedding` — **fused lexical + semantic search**.
   - at `ready` — **chat** and metrics.

   This staging is the point of the pipeline order below. On a large repo the embedding
   stage dominates wall-clock time; gating everything behind it would mean staring at a
   progress bar for ten minutes before the product does anything.

## Indexing pipeline (the core of the product)

A Celery job in `worker` with ordered stages. Each stage updates repository status +
progress so the UI reflects it live.

```
queued
  │
  ▼
cloning        git clone into worker storage; persist a `file` row per tracked path
  │            with its raw content
  │            → FILE TREE USABLE FROM HERE
  ▼
parsing        walk every py/js/ts file; Tree-sitter per language
  │            extract: functions, classes, methods → SYMBOL TABLE
  │            plus imports, and call sites (M7)
  ▼
graphing       resolve what parsing extracted → dependency_edge (file → file imports)
  │            and reference_edge (entity → entity calls, M7)
  ▼
chunking       AST-aware chunks (by function/class, never fixed windows), each with
  │            its context prefix; content_tsv generates itself over embed_text
  │            → LEXICAL SEARCH USABLE FROM HERE
  ▼
embedding      embed each chunk's embed_text (text-embedding-3-small, 1536 dims) → pgvector
  │            ← the slow, network-bound stage. Deliberately last.
  │            → FUSED LEXICAL + SEMANTIC SEARCH USABLE FROM HERE
  ▼
metrics        basic aggregates: file count, function count, total LOC,
  │            language breakdown, largest files
  ▼
ready          everything persisted; chat + dashboard unlock
```

**Why this order.** Everything deterministic and local (symbol table, graphs, chunks +
their lexical index) runs before the one stage that makes network calls against an external
API. Two payoffs: the user gets a usable product early in the run rather than only at the
end, and a failure in `embedding` leaves a repo that is still searchable and browsable
instead of one that is worthless.

That second payoff is why `chunk.embedding` is nullable and the stage selects
`WHERE embedding IS NULL` — a retry after a crash or provider outage resumes where it
stopped instead of re-embedding everything, and a repo stuck part-way through still answers
lexical search correctly.

`graphing` moved ahead of `chunking`/`embedding` for the same reason — it's pure import
resolution over data `parsing` already produced, so there's no reason for it to sit behind
the slowest stage.

On any failure in `cloning` through `chunking`: `status=failed`, store the error, surface a
retry action — these stages are deterministic and local, so a failure means the repo really
is broken.

**`embedding` is the one exception, deliberately.** It's the first stage that can fail for
reasons that have nothing to do with the repo — a provider outage or a bad key, not a bug. A
failure there is caught, logged, and leaves `status=embedding` rather than `failed`: the repo
stays exactly as searchable as it was (lexical still works, chat doesn't exist until `ready`),
and a later retry resumes via `WHERE embedding IS NULL` instead of needing a full re-index.
Marking it `failed` would have made `retry_repository`'s existing cleanup delete every `File`
row — cascading away every embedding already paid for — over a transient external error.

## Stage responsibilities

- **cloning** — `core/github`. Shallow clone where possible. Also enumerates every
  tracked file (`git ls-files` — respects `.gitignore`) and writes a `file` row per
  path with its raw `content` (or `is_binary=true` with no content), powering the
  file tree browser independent of parsing.
- **parsing** — `indexer`. One extractor per language (py/js/ts) via Tree-sitter.
  Produces plain structured data → `code_entity` rows (the symbol table), import
  specifiers, and — from M7 — call sites. Note the call-site pass has to descend *into*
  function bodies, which the symbol-table walk deliberately does not.
- **graphing** — `indexer`. Resolve imports to target files (`dependency_edge`) and callee
  names to target entities (`reference_edge`). Powers `list_dependencies`, `get_callers`,
  the M7 graph leg, the M8 repo map, and the V2 architecture view. Pure in-memory
  resolution over `parsing` output — fast, no I/O beyond the DB write.
- **chunking** — `indexer`. Chunk by AST node; each chunk keeps file, line range, and
  owning entity, and carries a context prefix (path › class › signature). `content_tsv` is
  a Postgres *generated* column over `embed_text`, so the lexical index maintains itself as
  rows are written — no separate indexing step, no cost to this stage. See `RETRIEVAL.md`
  — both chunking rules are non-negotiable.
- **embedding** — `core/ai`, `text-embedding-3-small` at 1536 dims, one request per page
  of 200 chunks. Resumable via `WHERE embedding IS NULL`. Skip by file `content_hash` so
  re-indexing unchanged files costs nothing. The only network-bound stage.
- **metrics** — pure aggregation over already-extracted data; cheap to recompute.
  V1 keeps this basic — counts, languages, largest files. No dead-code/complexity/etc.

## Re-indexing

Re-importing or refreshing re-runs the pipeline. Skip unchanged files by `content_hash`;
only changed files are re-chunked and re-embedded — saves time and embedding cost.
