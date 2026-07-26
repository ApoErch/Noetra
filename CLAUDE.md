# Noetra — Engineering Intelligence Platform

Point Noetra at a GitHub repo → it clones, indexes, and lets you **chat with and
search** the codebase through an AI agent that answers with exact file + line
citations. Grounded QA over a codebase is the product; **retrieval quality is the
whole game** (see `docs/RETRIEVAL.md`).

## Session start (do this first)

Read the newest entry in `docs/SESSION_LOG.md` to pick up where the last session left
off. If it doesn't exist yet, skip this. (The `/endsession` skill writes those entries.)

## How I work with you — mentoring mode (applies to every task)

I'm building Noetra to **learn**, so teach as you build.

**IMPORTANT: explain your reasoning, not just your conclusion.** When you decide we need
something (e.g. "add a session secret"), first tell me *how you got there and why* —
your approach to the problem — then implement. Never hand me a decision with no path to it.

**Who I am:** junior SWE, ~1.5 YOE. Building this to learn, then to compete for junior
SWE and junior AI / data / ML roles at strong (tier A–S) companies.
- **I know the basics of (don't re-teach):** React, TypeScript, FastAPI, Python,
  C#/Unity, LangGraph.
- **New to me (explain the first time it appears):** Celery, Redis, Tree-sitter,
  pgvector, deep Docker, AWS — and most of auth/security, networking, databases,
  distributed systems, and retrieval. **When unsure whether I know something, assume I
  don't.** Knowing FastAPI basics ≠ knowing web-session security — don't skip a concept
  just because it sits next to one I know.

**Keep explanations tight:** simple words + one concrete example or analogy; define any
term in the same breath; **name the pattern** ("this is reciprocal rank fusion") so I can
look it up; give the real trade-off (what else we could do, why this wins); link the
**official** docs. Idea + reason + example + link — no walls of text.

**Teach system design, not just this file.** For architectural choices, name the general
pattern, the alternatives, and where the same trade-off shows up in industry. Call out
classic trade-offs by name (consistency vs. availability, latency vs. throughput,
coupling vs. duplication).

**When you add or wire a new file, show me how it fits:** where it lives, who calls it /
what it calls, the data flow through it, and what I'll see when it runs. A quick arrow
sketch is ideal.

**IMPORTANT — do it the standard way, not the fancy way.** Use the correct,
industry-standard approach for each area (auth, DB access, API design, error handling),
and tell me when something *is* the standard. **Don't over-engineer** — no extra
abstraction, defensive layers, or speculative generality I didn't ask for. When you
finish an area, finish it cleanly: no dead code, stubs, or half-wired leftovers.

**Approval gate:** before running any command, state it, say what it does and why, then
**wait for my go-ahead**. Never explain-then-run in the same turn.

## V1 scope

GitHub OAuth login (required before any import) · repo import — public URL (plain clone) or private repo (clone with the user's decrypted token) · background indexing
pipeline (clone+lexical index → parse+symbols → graph → chunk → embed → metrics) ·
**hybrid retrieval** (lexical + semantic, RRF-fused) · a **retrieval eval
set** that keeps that hybrid honest · streamed LangGraph chat agent with citations ·
hybrid search · basic dashboard (files, functions, LOC, language breakdown, largest files).

That arrow chain is the **pipeline order** — what happens during a single indexing run of
one repo, every time. Don't confuse it with the **build order** below, which is the
sequence in which the code gets written over the life of the project. They're separate
decisions that happen to follow the same principle: *cheap and deterministic first, slow
and expensive last.*

In the pipeline, that means the lexical index, symbol table, and import graph all finish
before chunking and embedding start — so a repo becomes searchable minutes into indexing
rather than only when the slowest stage completes.

**Languages at launch:** Python, JavaScript, TypeScript. Nothing else.

**Deferred to V2 (leave clean extension points, don't build):** security scanner,
architecture graph viz (React Flow), AI code/PR review, advanced metrics, call-graph
extraction (`find_references`/`find_callers` — build only if the M6 agent demonstrably
needs it, see `docs/RETRIEVAL.md`).

## Stack

React + TS + Tailwind + Monaco + TanStack Query · FastAPI + Pydantic v2 + SQLAlchemy ·
Celery + Redis · Postgres + pgvector · Tree-sitter · **OpenAI API for chat + embeddings**
(`text-embedding-3-small`, 1536 dims). Both **must stay behind one interface in `core/ai`**
so the provider is swappable — nothing outside `core/ai` imports the OpenAI SDK.
Docker Compose local, GitHub Actions CI, AWS deploy (`docs/DEPLOYMENT.md`).

## Module boundaries (respect these when adding code)

Structure is already built — read the real tree from the repo. What matters is intent:
- `backend/api` — FastAPI: auth, REST, retrieval + agent endpoints. **HTTP boundary
  only; no slow work inline.**
- `backend/worker` — Celery tasks: the indexing pipeline. **All slow / repo-touching
  work lives here.**
- `backend/core` — shared: DB models, schemas, GitHub client, `ai` + embeddings,
  retrieval, agent graph.
- `backend/indexer` — pure parsing (Tree-sitter, AST chunking, symbol table, dep graph).
  **No DB, no HTTP** — stays unit-testable in isolation.

**Rules:** `api` and `worker` import `core` and `indexer`; `indexer` imports neither.
Retrieval lives **only** in `core/retrieval`.

## Build order (each milestone runs end-to-end before the next)

**Ordering principle: build retrievers in cost order — cheap first, measure, then buy the
expensive one.** Lexical search is cheap to build and cheap to throw away. Embeddings are
neither: changing the chunking strategy means re-embedding the whole corpus, and that
strategy is exactly what tends to change after first contact with real failing queries.
So the eval set and the citation UI land *before* pgvector, and semantic retrieval has to
earn its slot by moving `recall@k` on questions the cheap retrievers demonstrably fail.

This works because `file.content` is already persisted at clone time (M3). A Postgres
`tsvector` index over it is one migration and zero pipeline cost, which is enough
retrieval to build the agent and the whole citation path against.

Progress is logged in `docs/LEARNING_LOG.md`. **Done: M1 Skeleton, M2 Auth, M3 Import +
clone, M4 Parse + extract, M5 Eval + hybrid search. Next up: M6.**

4. ~~Parse + extract (Tree-sitter py/js/ts → files + entities = the symbol table), plus the
   two things that ride along free with it: the **lexical index** (`tsvector` + `pg_trgm`)
   and the **dependency graph** (resolve the imports the parser already extracted). No AI
   calls in this milestone at all.~~ **Done.**
5. ~~**Eval harness** (~40 questions with known answer locations → `recall@k`) + search
   endpoint & UI over lexical + structural. First end-to-end `file:line` citations, and
   the scoreboard every later retrieval change is judged against.~~ **Done** — 46 questions,
   line-level `recall@5` 0.76 / `recall@20` 0.81. **AST chunking was pulled forward from M7
   into this milestone** (it earns its place through exact citations and smaller agent
   payloads, independently of embeddings — and it is a prerequisite for them anyway), so
   the pipeline now runs `cloning → parsing → graphing → chunking`. **Structural retrieval
   was subsequently removed** after an ablation against this same eval set showed it moving
   recall@5 by only +0.04, concentrated in one edge case — see `docs/RETRIEVAL.md`'s
   decision record. `search()` is lexical-only now.
6. Chat agent (LangGraph: `code_search`, `read_file`, `list_dependencies`; multi-step loop,
   streamed, cited) — ship-quality MVP. All three tools have real data behind them by now.
   The agent is oriented by a **repo map** — a PageRank-ranked, AI-free "table of contents"
   (top symbols per file, ranked by import-graph centrality) built from `code_entity` +
   `dependency_edge` and placed in the stable prompt prefix, so the agent's first move is
   informed instead of a blind keyword guess. See `RETRIEVAL.md`.
7. **(Conditional)** Embeddings over the existing chunks (→ pgvector) + the semantic leg and
   reranking — built **only if** the M5 eval set shows the cheap lexical + agent + repo-map
   stack actually failing questions that embeddings would fix. Chunking already shipped in
   M5, so what remains here is purely the embedding leg. Entered with a measured baseline
   and a known list of failures; if the baseline already clears the bar, this milestone may
   never be built. **No retriever joins the fusion without a `recall@k` movement that
   justifies it** — the same rule that got structural retrieval cut in M5. *Current
   evidence against it: only 2 of 46 eval questions fail to surface the correct file at
   all.*
8. Basic metrics + dashboard

Milestone 7 is where the old plan's steps 5–6 went, and the old "chat v1 single-shot RAG
then chat v2 agent" split collapsed into milestone 6 — see `docs/FEATURES.md` §4 for why
shipping the agent directly is the smaller piece of work, not the larger one.

## Conventions

- Strict typing both sides (mypy / TS strict). REST under `/api/v1`; validate with
  Pydantic at the boundary.
- Anything touching a repo is a Celery task in `worker` — never inline in `api`.
- Status is first-class: `queued → cloning → parsing → graphing → chunking → embedding →
  metrics → ready | failed`, surfaced to the UI. (Same set of states as before; `graphing`
  moved ahead of `chunking`/`embedding` to match the pipeline order above. The
  `RepositoryStatus` enum in `core/models.py` already holds every value — only the order
  the worker advances through them changes.)
- Secrets via env only; keep `.env.example` current.
- Everything scoped by `repository_id`; ownership checked on every read.

## Reference docs

| File | Contents |
|------|----------|
| `docs/RETRIEVAL.md` | **Read first.** Hybrid retrieval design — the core engineering |
| `docs/ARCHITECTURE.md` | Components, data flow, service responsibilities |
| `docs/WORKFLOW.md` | User journey + indexing pipeline, step by step |
| `docs/DATA_MODEL.md` | Database schema and core entities |
| `docs/FEATURES.md` | Per-feature spec + V2 notes |
| `docs/SETUP.md` | Local dev: services, env vars, first-run commands |
| `docs/DEPLOYMENT.md` | AWS deployment target (phased) |
| `docs/LEARNING_LOG.md` | Interview-ready log, one entry per milestone |
| `docs/CONCEPTS.md` | Personal glossary of concepts new to me |
| `docs/SESSION_LOG.md` | Session-to-session handoff (written by `/endsession`) |