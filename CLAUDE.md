# Noetra — Engineering Intelligence Platform

> Working name: **Noetra** (rename freely). A SaaS product that connects to a GitHub
> repository, indexes it in the background, and lets you *talk to and search* the
> codebase through an AI agent that answers with exact file + line citations.

**This file is the entry point.** Read it fully before writing code, then pull in the
relevant file from `docs/` for detail.

---

## What we're building (one line)

Point Noetra at a repo → it clones, parses, and indexes the codebase → the user gets an
AI agent they can ask questions of and search in plain English, grounded in the real
code with citations.

## How to work with me — mentoring mode (applies from message one, always)

I'm building this to learn, not just to ship it. This is a standing instruction
across every milestone from the first commit onward — it does not wait until things
get "advanced."

**Explain, unprompted, whenever something crosses the junior-engineer line:**
- Any architectural decision — why this component boundary, why data flows this way,
  why async work is a job and not inline, why hybrid retrieval over pure vector search
- Any function or class whose logic isn't obvious top-to-bottom — non-trivial control
  flow, concurrency, recursion, anything with more than one "clever" step
- Any multi-step sequence of events — the indexing pipeline, a request lifecycle, the
  agent's tool-calling loop — walk through *why* it happens in that order, not just
  that it works
- Any new tool, library, or pattern not yet used in this project (Tree-sitter, Celery,
  pgvector, LangGraph, RRF, etc.) the first time it's introduced

**Don't over-explain:** boilerplate, standard CRUD, straightforward glue code —
anything a mid-level engineer reads in two seconds without a note. Calibrate to real
complexity, the same judgment a good mentor uses to decide when a junior needs a
pointer versus when they're fine alone.

**What "explaining" means:**
- Say *why*, not just *what*
- Name the underlying concept or pattern explicitly (e.g. "this is the Repository
  pattern," "this is reciprocal rank fusion," "this is a producer/consumer queue") so
  it's something to look up later and recognize in an interview
- Walk real tradeoffs instead of asserting the answer
- Flag explicitly when something is a good interview talking point — a line like
  "this is worth being able to explain if asked" is enough

**After each milestone**, append a short entry to `docs/LEARNING_LOG.md`: what was
built, the core concept(s), and a 2-3 sentence recruiter-ready explanation.

Goal: by the end, every non-trivial piece of this project should be something I can
explain unprompted, at a mid-level-engineer standard, to an interviewer.

## The core bet

This product is *grounded question-answering over a codebase*. Chat + search are the
product; everything else is a read on top of the same index. **Retrieval quality is the
whole game.** See `docs/RETRIEVAL.md` — it's the most important doc here.

## V1 scope (build this)

1. GitHub OAuth login
2. Repository import — public URL, private repo (OAuth), or ZIP upload
3. Background indexing pipeline (clone → parse → chunk → embed → symbol/graph → basic metrics)
4. **Hybrid retrieval** (lexical + structural + semantic)
5. AI Chat — LangGraph agent over the retriever, streamed, with citations
6. Natural-language semantic search
7. Repository dashboard — **basic metrics only** (files, functions, LOC, language breakdown, largest files)

**Languages at launch: Python, JavaScript, TypeScript.** Nothing else in V1.

## Explicitly OUT of scope for V1 (deferred to V2)

- **Security scanner**
- **Architecture graph view** (React Flow) — the dependency data is still built in V1,
  but no visualization is shipped
- **AI Code Review** (per-file issue detection)
- **Pull Request Review**
- **Advanced metrics** (dead code, duplicates, unused imports, complexity, ownership)

Leave clean extension points, nothing more.

---

## Tech stack

**Frontend:** React + TypeScript, Tailwind CSS, Monaco Editor, TanStack Query.
(React Flow deferred with the V2 architecture view.)

**Backend:** **FastAPI (Python)**, Pydantic v2, SQLAlchemy.

**Worker / queue:** **Celery + Redis** (broker + result/status).

**Database:** PostgreSQL + **pgvector**.

**Parsing:** Tree-sitter (python, javascript, typescript grammars).

**AI:** Anthropic API (chat + summaries). **Embeddings: provider TBD** — chosen at
implementation time (candidates: a code-specialized model like Voyage `voyage-code`,
OpenAI, or a local model). Keep the embedding provider behind one interface in `core/ai`
so the decision is deferred and swappable.

**Agent:** **LangGraph** — the chat agent with retrieval tools.

**Infra:** GitHub OAuth + GitHub API, Docker (essential), Docker Compose (local),
GitHub Actions (CI). **Deployed live on AWS** — right-sized, see `docs/DEPLOYMENT.md`.

---

## Repository structure (target)

Python monorepo (one venv via `uv` or Poetry) + a separate React app.

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

`api` and `worker` both import `core` and `indexer`. `indexer` is pure logic (no DB, no
HTTP) so it's unit-testable. Frontend types are generated from the FastAPI OpenAPI schema.

---

## Build order (milestones)

Each milestone runs end-to-end before the next.

1. **Skeleton** — monorepo, Docker Compose (Postgres + Redis), FastAPI boots, React boots, `core` package with DB + models.
2. **Auth** — GitHub OAuth, session, "my repositories" from the GitHub API.
3. **Import + clone** — accept a repo, enqueue a Celery job, clone into worker storage, persist `Repository` with status.
4. **Parse + extract** — Tree-sitter walk (py/js/ts), extract files + entities (functions/classes) into DB. This builds the **symbol table**.
5. **AST chunking + embeddings** — chunk by function/class, embed, store in pgvector. Build the dependency edges here too.
6. **Hybrid retrieval** — lexical + structural + semantic, fused. Semantic search endpoint + UI. (`docs/RETRIEVAL.md`)
7. **Chat v1 — single-shot RAG** over the hybrid retriever. Streamed, with citations. *This is a usable MVP checkpoint — ship-quality before moving on.*
8. **Chat v2 — LangGraph agent** with tools (`code_search`, `find_symbol`, `read_file`, `list_dependencies`), multi-step retrieval loop.
9. **Basic metrics + dashboard** — aggregate counts + language breakdown + largest files; repository dashboard UI.

Milestones 3–9 all hang off the indexing pipeline — see `docs/WORKFLOW.md`.

---

## Conventions

- **Python** backend, **TypeScript** frontend. Strict typing both sides (mypy / TS strict).
- **API:** REST under `/api/v1`. Request/response models are Pydantic; validated at the boundary.
- **Async work is never inline** — anything touching a repo (clone/parse/embed) is a
  Celery task in `worker`, never in the request cycle.
- **Status is first-class** — every repo has an indexing status
  (`queued → cloning → parsing → chunking → embedding → graphing → metrics → ready | failed`)
  surfaced to the UI.
- **AI + embeddings are isolated** — all Anthropic and embedding calls go through
  `core/ai`; prompts, retries, token budgeting live there.
- **GitHub access isolated** — all git/GitHub calls go through one client in `core`.
- **Retrieval is one module** — `core/retrieval` owns the hybrid retriever; the agent
  and the search endpoint both call it. No retrieval logic leaks elsewhere.
- **Secrets** via env only; `.env.example` stays current.
- **Everything** is scoped by `repository_id`; ownership checked on every read.

---

## Reference docs

| File | Contents |
|------|----------|
| `docs/RETRIEVAL.md` | **Read first after this.** The hybrid retrieval design — the core engineering. |
| `docs/ARCHITECTURE.md` | System components, data flow, service responsibilities |
| `docs/WORKFLOW.md` | User journey + the background indexing pipeline, step by step |
| `docs/DATA_MODEL.md` | Database schema and core entities |
| `docs/FEATURES.md` | Per-feature spec for each V1 surface + V2 notes |
| `docs/SETUP.md` | Local dev: services, env vars, first-run commands |
| `docs/DEPLOYMENT.md` | Live deployment target on AWS (right-sized, phased) |
| `docs/LEARNING_LOG.md` | Recruiter-ready log of concepts learned per milestone |
