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
4. **Explore** — the UI opens a repo at **`ready`**, and not before. Until then the card in
   the repo list shows the stage it has reached, a step counter and a progress bar.

   The *pipeline* still unlocks capabilities progressively, and the API still gates on data
   rather than on `status` — that part is unchanged and is what the order below is for:
   - after `cloning` — `GET /repos/{id}/files` can serve the **file tree**. Needs only
     `file.path` / `file.content`, which clone persists.
   - after `chunking` — **lexical search**. `search()` runs over `chunk.content_tsv`, so it
     needs chunks to exist; the `tsvector` is a generated column, so there's no separate
     index step once they do. `api/chat.py` gates chat on exactly this (does the repo have
     any chunks), never on `status`.
   - after `embedding` — **fused lexical + semantic search**.
   - at `ready` — metrics, and the repo becomes openable.

   **Why the product waits when the API wouldn't.** A workspace whose Dashboard tab is empty
   and whose Chat tab answers "still indexing" is a worse experience than a disabled Open
   button that says why. One completion state is easier to explain and easier to trust. The
   cost is real and accepted: on a large repo the wait now includes the network-bound
   embedding stage. `isOpenable()` in `web/src/lib/repos.ts` is the single place that decides
   this — see `CONCEPTS.md` B28 for the trade-off and the revisit trigger.

## Indexing pipeline (the core of the product)

A Celery job in `worker` with ordered stages. Each stage updates repository status +
progress so the UI reflects it live.

```
queued
  │
  ▼
cloning        git clone into worker storage; persist a `file` row per tracked path
  │            with its raw content
  │            → FILE TREE SERVABLE FROM HERE (the UI still waits for `ready`)
  ▼
parsing        walk every py/js/ts file; Tree-sitter per language
  │            extract: functions, classes, methods → SYMBOL TABLE
  │            plus imports; a second walk descends into bodies for call sites
  ▼
graphing       resolve what parsing extracted → dependency_edge (file → file imports)
  │            and reference_edge (entity → entity calls, name-resolved in
  │            confidence tiers; an ambiguous name resolves to nothing)
  ▼
chunking       AST-aware chunks (by function/class, never fixed windows), each with
  │            its context prefix; content_tsv generates itself over embed_text
  │            → LEXICAL SEARCH + CHAT SERVABLE FROM HERE
  ▼
embedding      embed each chunk's embed_text (text-embedding-3-small, 1536 dims) → pgvector
  │            ← the slow, network-bound stage. Deliberately last.
  │            → FUSED LEXICAL + SEMANTIC SEARCH SERVABLE FROM HERE
  ▼
metrics        basic aggregates written as `metric` rows: file count, function
  │            count, total LOC, language breakdown, largest files
  ▼
ready          everything persisted; the repo becomes openable in the UI
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
is broken. That retry is destructive by design: it deletes every `file` row and re-clones,
because a broken index is not worth resuming from.

**`embedding` is the one exception, deliberately.** It's the first stage that can fail for
reasons that have nothing to do with the repo — a provider outage or a bad key, not a bug. A
failure there is caught, logged, and leaves `status=embedding` rather than `failed`: the repo
stays exactly as searchable as it was (lexical still works, chat doesn't exist until `ready`),
and a later retry resumes via `WHERE embedding IS NULL` instead of needing a full re-index.
Marking it `failed` would have made `retry_repository`'s existing cleanup delete every `File`
row — cascading away every embedding already paid for — over a transient external error.

Which is why the API offers a second, non-destructive restart: a repo parked at `embedding`
or `metrics` re-runs only the pipeline's tail (`worker.tasks.resume_indexing` →
`finalize_repository`), keeping every file, symbol, edge and chunk. The UI shows it as
**Resume** rather than Retry, and only when the Redis index lock is *not* held — `status`
records the furthest stage reached and cannot by itself tell "still embedding" from "stopped
while embedding", so `GET /repos` reports the lock as `is_indexing`.

## Stage responsibilities

- **cloning** — `core/github`. Shallow clone where possible. Also enumerates every
  tracked file (`git ls-files` — respects `.gitignore`) and writes a `file` row per
  path with its raw `content` (or `is_binary=true` with no content), powering the
  file tree browser independent of parsing.
- **parsing** — `indexer`. One extractor per language (py/js/ts) via Tree-sitter.
  Produces plain structured data → `code_entity` rows (the symbol table), import
  specifiers, and call sites. The call-site pass is a **separate walk** (`indexer/calls.py`)
  because it has to descend *into* function bodies, which the symbol-table walk deliberately
  does not — the two concerns stay in their own modules rather than one walk growing a flag.
- **graphing** — `indexer`. Resolve imports to target files (`dependency_edge`) and callee
  names to target entities (`reference_edge`), in that order — the call graph's middle
  confidence tier is "defined in a file this one imports", so it reuses the import edges just
  resolved. Powers the `list_dependencies` / `find_references` agent tools, the repo map, and
  the V2 architecture view. Pure in-memory resolution over `parsing` output — one repo-wide
  `name -> entity` index is built up front rather than scanned per call, since a call graph
  runs an order of magnitude more lookups than import resolution.
- **chunking** — `indexer`. Chunk by AST node; each chunk keeps file, line range, and
  owning entity, and carries a context prefix (path › class › signature). `content_tsv` is
  a Postgres *generated* column over `embed_text`, so the lexical index maintains itself as
  rows are written — no separate indexing step, no cost to this stage. See `RETRIEVAL.md`
  — both chunking rules are non-negotiable.
- **embedding** — `core/ai`, `text-embedding-3-small` at 1536 dims, one request per page
  of 200 chunks. Resumable via `WHERE embedding IS NULL`. Skip by file `content_hash` so
  re-indexing unchanged files costs nothing. The only network-bound stage.
- **metrics** — `worker/metrics.py`. Five SQL aggregates over already-extracted data,
  written as `metric` rows and read back by the dashboard, so a 36,000-file repo is counted
  once at index time rather than on every dashboard poll. Sets `status = ready` — the only
  place that does. V1 keeps this basic: counts, languages, largest files. No
  dead-code/complexity/etc.

  One honest limit: `file.language` and `file.loc` are only populated for the parsed
  languages, so total LOC, the language breakdown and the largest-files list describe
  **source** files. `file_count` reports both totals so the difference is visible rather
  than silently swallowed.

## Re-indexing

Re-importing or refreshing re-runs the pipeline. Skip unchanged files by `content_hash`;
only changed files are re-chunked and re-embedded — saves time and embedding cost.
