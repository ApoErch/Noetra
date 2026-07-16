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
                         Tree-sitter     Voyage / Anthropic   Git / GitHub API
                         (parse+chunk)   (embed / summarize)  (clone / metadata)
```

## Components

**web** — React SPA. Talks only to the REST API. Owns dashboard, chat, search, Monaco
viewer. Watches repository status while indexing runs.

**api** — FastAPI. Stateless request layer: auth, repo CRUD, serves indexed data, runs
the `/search` endpoint via `core/retrieval`, and hosts the **LangGraph agent** for chat
(streamed). Enqueues indexing jobs; never does heavy work inline.

**worker** — Celery. Owns the indexing pipeline (`WORKFLOW.md`): clone, parse, chunk,
embed, build symbol table + dependency edges, compute basic metrics. Long-running,
horizontally scalable.

**PostgreSQL (+ pgvector)** — single source of truth: users, repositories, files,
code entities (symbol table), chunks + embeddings, dependency edges, metrics. pgvector
keeps embeddings in the same DB — no second datastore in V1.

**Redis** — Celery broker + task status/progress. Optional cache for dashboard reads.

## Shared logic (`core` + `indexer`)

- `core/db` — SQLAlchemy models + session.
- `core/github` — the only place that talks to git/GitHub.
- `core/ai` — the only place that calls Anthropic and the embedding provider.
- `core/retrieval` — the hybrid retriever (`RETRIEVAL.md`). Used by both the search
  endpoint and the agent tools.
- `core/agent` — the LangGraph graph + tool definitions.
- `indexer` — Tree-sitter parsing, AST chunking, symbol extraction, dependency graph.
  Pure logic: no DB, no HTTP, unit-testable in isolation.

## Data flow

- **Read (dashboard / search):** web → api → Postgres (via retrieval) → web.
- **Import:** web → api creates `Repository(status=queued)` → enqueues Celery job →
  returns immediately. web watches status.
- **Indexing:** worker consumes the job, advances status per stage, writes results,
  sets `ready` (or `failed` + error).
- **Chat:** web → api → LangGraph agent → agent calls retrieval tools in a loop →
  Anthropic → streamed answer with `file:line` citations → web.

## Boundaries / rules

- The request cycle never clones, parses, or embeds — always a Celery task.
- All Anthropic + embedding calls go through `core/ai`.
- All git/GitHub access goes through `core/github`.
- All retrieval goes through `core/retrieval`.
- `indexer` stays pure (no side effects) for testability.
- Everything scoped by `repository_id`; ownership checked on every read.
