# Noetra — Engineering Intelligence Platform

> Working name: **Noetra**. A SaaS product that connects to a GitHub repository,
> indexes it in the background, and lets you *talk to and search* the codebase
> through an AI agent that answers with exact file + line citations.

**This file is the entry point.** Read it fully before writing code, then pull in the
relevant file from `docs/` for detail.

---

## How to work with me — mentoring mode (always applies)

I'm building this to learn, not just ship it. By the end I should be able to explain
how and where *everything* here works, unprompted, at an interview standard.

**Who I am:** Integrated master's, Computer Engineering & Informatics. 1.5 YOE junior
dev — comfortable with React, TypeScript, C# (Unity), LangGraph, Python + FastAPI.
**New to this project:** Celery, Redis, Tree-sitter, pgvector, Docker at depth, AWS.
Calibrate explanations to that split — don't re-explain what I already know from work.

**Approval gate:** Before running any command, however small — state it, explain what
it does and why (see below), then **wait for my go-ahead**. Never explain-then-run in
the same turn.

**Explain, unprompted, when something crosses the junior line:** architectural
decisions (why this boundary, why async-as-a-job, why hybrid retrieval), non-trivial
logic (concurrency, recursion, multi-step control flow), multi-step sequences (the
indexing pipeline, a request lifecycle, the agent loop), and any tool/pattern new to
this project the first time it appears. Skip boilerplate, CRUD, and anything in my
known-experience list.

**What "explaining" means:** say *why* not just *what*; name the pattern explicitly
(e.g. "this is reciprocal rank fusion") so it's recognizable later; walk real
tradeoffs; flag good interview talking points; cite the specific project doc it's
grounded in (e.g. "per `docs/WORKFLOW.md`"); link the **official** docs for any
tool/library/CLI flag involved (Docker docs, FastAPI docs, Celery docs — not
tutorials/blogs). Keep it tight: idea + reason + link, nothing more.

**After each milestone:** append an entry to `docs/LEARNING_LOG.md` — what was built,
core concept(s), 2-3 sentence recruiter-ready explanation.

---

## What we're building

Point Noetra at a repo → it clones, parses, and indexes the codebase → the user gets
an AI agent to ask questions of and search in plain English, grounded in the real
code with citations.

**The core bet:** grounded QA over a codebase. Chat + search are the product;
everything else reads off the same index. **Retrieval quality is the whole game** —
see `docs/RETRIEVAL.md`, read it right after this file.

## V1 scope

1. GitHub OAuth login
2. Repository import — public URL, private repo (OAuth), or ZIP upload
3. Background indexing pipeline (clone → parse → chunk → embed → symbol/graph → metrics)
4. **Hybrid retrieval** (lexical + structural + semantic)
5. AI Chat — LangGraph agent over the retriever, streamed, with citations
6. Natural-language semantic search
7. Repository dashboard — basic metrics only (files, functions, LOC, language
   breakdown, largest files)

**Languages at launch:** Python, JavaScript, TypeScript. Nothing else in V1.

**Deferred to V2:** security scanner, architecture graph view (React Flow — data is
built in V1, no viz), AI code review, PR review, advanced metrics (dead code,
duplicates, unused imports, complexity, ownership). Leave clean extension points,
nothing more.

---

## Tech stack

- **Frontend:** React + TypeScript, Tailwind, Monaco Editor, TanStack Query (React
  Flow deferred to V2)
- **Backend:** FastAPI, Pydantic v2, SQLAlchemy
- **Worker/queue:** Celery + Redis (broker + result/status)
- **Database:** PostgreSQL + pgvector
- **Parsing:** Tree-sitter (python, javascript, typescript grammars)
- **AI:** Anthropic API (chat + summaries); embeddings provider TBD, isolated behind
  one interface in `core/ai` so it's swappable
- **Agent:** LangGraph, chat agent with retrieval tools
- **Infra:** GitHub OAuth + API, Docker + Compose, GitHub Actions CI, deployed on
  AWS (right-sized, phased — see `docs/DEPLOYMENT.md`)

## Repository structure (target)

Python monorepo (one venv via `uv`) + a separate React app.

```
/web                 # React + TS frontend (own package.json)
/backend
  /api               # FastAPI app: auth, REST, retrieval, agent endpoint
  /worker            # Celery tasks: the indexing pipeline
  /core              # shared: db models, schemas, github client, ai + embeddings, retrieval, agent graph
  /indexer           # tree-sitter parsing, AST chunking, symbol table, dependency graph
  pyproject.toml
docker-compose.yml
/docs
```

`api` and `worker` both import `core` and `indexer`. `indexer` is pure logic (no DB,
no HTTP) — unit-testable. Frontend types generate from the FastAPI OpenAPI schema.

---

## Build order (milestones, each runs end-to-end before the next)

1. **Skeleton** — monorepo, Compose (Postgres + Redis), FastAPI boots, React boots,
   `core` package with DB + models
2. **Auth** — GitHub OAuth, session, "my repositories" from GitHub API
3. **Import + clone** — accept a repo, enqueue Celery job, clone into worker storage,
   persist `Repository` with status
4. **Parse + extract** — Tree-sitter walk (py/js/ts), extract files + entities into
   DB — builds the **symbol table**
5. **AST chunking + embeddings** — chunk by function/class, embed, store in pgvector;
   build dependency edges here too
6. **Hybrid retrieval** — lexical + structural + semantic, fused; search endpoint +
   UI (`docs/RETRIEVAL.md`)
7. **Chat v1** — single-shot RAG over the hybrid retriever, streamed, with citations
   — usable MVP checkpoint, ship-quality before moving on
8. **Chat v2** — LangGraph agent with tools (`code_search`, `find_symbol`,
   `read_file`, `list_dependencies`), multi-step retrieval loop
9. **Basic metrics + dashboard** — aggregate counts, language breakdown, largest
   files, dashboard UI

Milestones 3–9 hang off the indexing pipeline — see `docs/WORKFLOW.md`.

## Conventions

- Python backend, TypeScript frontend — strict typing both sides (mypy / TS strict)
- REST under `/api/v1`; Pydantic models validated at the boundary
- Async work never inline — anything touching a repo is a Celery task in `worker`
- Status is first-class: `queued → cloning → parsing → chunking → embedding →
  graphing → metrics → ready | failed`, surfaced to the UI
- AI + embeddings isolated in `core/ai`; GitHub access isolated in one `core` client
- Retrieval is one module — `core/retrieval` owns it; nothing leaks elsewhere
- Secrets via env only; `.env.example` stays current
- Everything scoped by `repository_id`; ownership checked on every read

## Reference docs

| File | Contents |
|------|----------|
| `docs/RETRIEVAL.md` | **Read first after this.** Hybrid retrieval design — the core engineering |
| `docs/ARCHITECTURE.md` | System components, data flow, service responsibilities |
| `docs/WORKFLOW.md` | User journey + background indexing pipeline, step by step |
| `docs/DATA_MODEL.md` | Database schema and core entities |
| `docs/FEATURES.md` | Per-feature spec for each V1 surface + V2 notes |
| `docs/SETUP.md` | Local dev: services, env vars, first-run commands |
| `docs/DEPLOYMENT.md` | Live deployment target on AWS (right-sized, phased) |
| `docs/LEARNING_LOG.md` | Recruiter-ready log of concepts learned per milestone |
