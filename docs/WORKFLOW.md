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
   - after `cloning` — the **file tree browser** and **lexical search**. Both need only
     `file.path` / `file.content`, which clone persists, plus the `tsvector` index built
     in the same stage. Neither waits on the symbol table or on embeddings.
   - after `parsing` — **symbol lookup** ("where is `createToken` defined?").
   - at `ready` — **chat**, full hybrid search, and metrics.

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
  │            with its raw content, and build the lexical index over it
  │            → FILE TREE + LEXICAL SEARCH USABLE FROM HERE
  ▼
parsing        walk every py/js/ts file; Tree-sitter per language
  │            extract: files, functions, classes, methods, imports  → SYMBOL TABLE
  ▼
graphing       resolve the imports parsing already extracted → dependency edges
  │            (data only in V1 — no visualization until V2)
  ▼
chunking       AST-aware chunks (by function/class, never fixed windows)
  │
  ▼
embedding      embed each chunk (OpenAI text-embedding-3-small) → vector in pgvector
  │            ← the slow, expensive, network-bound stage. Deliberately last.
  ▼
metrics        basic aggregates: file count, function count, total LOC,
  │            language breakdown, largest files
  ▼
ready          everything persisted; chat + full hybrid search + dashboard unlock
```

**Why this order.** Everything deterministic and local (lexical index, symbol table,
import graph) runs before the one stage that makes thousands of network calls and costs
money. Two payoffs: the user gets a usable product early in the run rather than only at
the end, and a failure in `embedding` leaves a repo that is still searchable and browsable
instead of one that is worthless.

`graphing` moved ahead of `chunking`/`embedding` for the same reason — it's pure import
resolution over data `parsing` already produced, so there's no reason for it to sit behind
the slowest stage.

On any failure: `status=failed`, store the error, surface a retry action.

## Stage responsibilities

- **cloning** — `core/github`. Shallow clone where possible. Also enumerates every
  tracked file (`git ls-files` — respects `.gitignore`) and writes a `file` row per
  path with its raw `content` (or `is_binary=true` with no content), powering the
  file tree browser independent of parsing. The `tsvector` column over `content` is a
  Postgres *generated* column, so the lexical index maintains itself as rows are
  written — there is no separate indexing step and no cost to this stage.
- **parsing** — `indexer`. One extractor per language (py/js/ts) via Tree-sitter.
  Produces plain structured data → files + `code_entity` rows (the symbol table).
- **graphing** — `indexer`. Resolve each import to a target file; write edges. Powers
  `list_dependencies` for the agent now, and the V2 architecture view later. Pure
  in-memory resolution over `parsing` output — fast, no I/O beyond the DB write.
- **chunking** — `indexer`. Chunk by AST node; each chunk keeps file, line range, and
  owning entity, and carries a context prefix (path › class › signature) for embedding.
  See `RETRIEVAL.md` — both rules are non-negotiable.
- **embedding** — `core/ai`, OpenAI `text-embedding-3-small` (1536 dims). Batched to
  control cost and stay inside rate limits. Cache by file `content_hash` so re-indexing
  unchanged files is cheap. The only stage that spends money per run.
- **metrics** — pure aggregation over already-extracted data; cheap to recompute.
  V1 keeps this basic — counts, languages, largest files. No dead-code/complexity/etc.

## Re-indexing

Re-importing or refreshing re-runs the pipeline. Skip unchanged files by `content_hash`;
only changed files are re-chunked and re-embedded — saves time and embedding cost.
