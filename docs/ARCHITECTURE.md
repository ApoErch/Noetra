# Architecture

## High-level

```
                ┌──────────────┐
   Browser ───► │  web (React) │
                └──────┬───────┘
                       │ REST /api/v1
                ┌──────▼───────┐        ┌──────────────┐
                │  api (FastAPI)│◄──────►│  PostgreSQL   │
                │  + LangGraph  │        │  + pgvector   │
                │    agent      │        └──────────────┘
                └──┬────────┬──┘
          enqueue  │        │ read/write
                   ▼        │
              ┌─────────┐   │           ┌──────────────┐
              │  Redis  │◄──┴──────────►│   worker     │
              │ (Celery)│    consume    │  (Celery)    │
              └─────────┘               └──────┬───────┘
                                               │
                              ┌────────────────┼────────────────┐
                              ▼                ▼                ▼
                         Tree-sitter       Gemini API       Git / GitHub API
                         (parse+chunk)   (chat / embed)     (clone / metadata)
                                        gemini-3.5-flash
                                        gemini-embedding-001
```

## Components

**web** — React SPA. Talks only to the REST API. Owns dashboard, chat, search, Monaco
viewer. Watches repository status while indexing runs.

**api** — FastAPI. Stateless request layer: auth, repo CRUD, serves indexed data, runs
the `/search` endpoint via `core/retrieval`, and hosts the **LangGraph agent** for chat
(streamed over SSE). Enqueues indexing jobs; never does heavy work inline.

The agent living in `api` rather than `worker` is deliberate: a chat turn is
**interactive** (the user is watching tokens stream) while everything in `worker` is
**batch** (nobody is waiting on a specific clone). Putting the agent behind a Celery queue
would mean polling for an answer that should be streaming. The "no slow work in `api`" rule
is really "no *repo-touching* work in `api`" — the agent only reads the DB.

**worker** — Celery. Owns the indexing pipeline (`WORKFLOW.md`): clone, parse, graph,
chunk, embed, compute basic metrics. Long-running, horizontally scalable.

**PostgreSQL (+ pgvector)** — single source of truth: users, repositories, files,
code entities (symbol table), chunks + embeddings, dependency + reference edges, metrics.
pgvector keeps embeddings in the same DB — no second datastore in V1.

**Redis** — Celery broker + task status/progress. Optional cache for dashboard reads.

## Shared logic (`core` + `indexer`)

- `core/db` — SQLAlchemy models + session.
- `core/github` — the only place that talks to git/GitHub.
- `core/ai` — the only place that imports any AI provider's SDK. Embeddings are
  Gemini-only (`embed_documents`/`embed_query`); chat is provider-switchable
  (`get_chat_model(provider)`, default Gemini, OpenAI/Anthropic available for testing the
  M8 agent). Two narrow interfaces — "generate a streamed completion given messages +
  tools" and "embed these strings" — which is the entire cost of switching providers later. This seam is worth keeping honest even with one provider,
  because retrieval quality depends on the embedding model.
  It also owns everything provider-shaped that would otherwise leak outward: batching, the
  `task_type` split (`RETRIEVAL_DOCUMENT` when indexing, `CODE_RETRIEVAL_QUERY` when
  querying), truncation to the model's 2048-token input cap, client-side L2 normalization,
  and free-tier rate limiting with 429 backoff.
- `core/retrieval` — the three retrieval legs + RRF fusion (`RETRIEVAL.md`). Used by both
  the search endpoint and the agent tools. No retrieval logic lives anywhere else.
- `core/agent` — the hand-rolled LangGraph `StateGraph`, the tool definitions, and the
  repo map.
- `indexer` — Tree-sitter parsing, AST chunking, symbol extraction, import + call graph
  resolution. Pure logic: no DB, no HTTP, unit-testable in isolation.

## Data flow

- **Read (dashboard / search):** web → api → Postgres (via retrieval) → web.
- **Import:** web → api creates `Repository(status=queued)` → enqueues Celery job →
  returns immediately. web watches status.
- **Indexing:** worker consumes the job, advances status per stage, writes results,
  sets `ready` (or `failed` + error).
- **Chat:** web → api → LangGraph agent → agent calls retrieval tools in a loop →
  Gemini → streamed answer with `file:line` citations → web. Tool-call status streams
  alongside tokens, so a multi-second loop reads as alive rather than hung.

## Rules

- The request cycle never clones, parses, or embeds — always a Celery task.
- All Gemini calls (chat + embeddings) go through `core/ai`.
- All git/GitHub access goes through `core/github`.
- All retrieval goes through `core/retrieval`.
- `indexer` stays pure (no side effects) for testability.
- Everything scoped by `repository_id`; ownership checked on every read.
