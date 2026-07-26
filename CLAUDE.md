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
**agentic RAG**: three retrieval legs (lexical + semantic, RRF-fused, plus graph
traversal) driven by an agent that picks its own strategy per query · a **retrieval eval
set** that keeps all three honest · streamed LangGraph chat agent with citations ·
hybrid search · basic dashboard (files, functions, LOC, language breakdown, largest files).

That arrow chain is the **pipeline order** (one indexing run) — not the **build order**
(`docs/BUILD_ORDER.md`). Both follow the same principle: *cheap and deterministic first,
slow and expensive last.*

**Languages at launch:** Python, JavaScript, TypeScript. Nothing else.

**Deferred to V2 (leave clean extension points, don't build):** security scanner,
architecture graph viz (React Flow), AI code/PR review, advanced metrics, reranking (no
free Gemini reranker exists — see `docs/RETRIEVAL.md`), more languages. Call-graph
extraction is **no longer** deferred; it's M7.

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
Retrieval lives **only** in `core/retrieval`. The AI SDK is imported **only** in `core/ai` —
no exceptions, that seam is the entire cost of swapping providers later.

## Conventions

- Strict typing both sides (mypy / TS strict). REST under `/api/v1`; validate with
  Pydantic at the boundary.
- Anything touching a repo is a Celery task in `worker` — never inline in `api`.
- Status is first-class: `queued → cloning → parsing → graphing → chunking → embedding →
  metrics → ready | failed`, surfaced to the UI. `RepositoryStatus` in `core/models.py`
  already holds every value.
- Secrets via env only; keep `.env.example` current.
- Everything scoped by `repository_id`; ownership checked on every read.

## Reference docs

| File | Contents |
|------|----------|
| `docs/RETRIEVAL.md` | **Read first.** Agentic RAG design — the core engineering |
| `docs/BUILD_ORDER.md` | Milestones, current status, ordering rationale — **read before starting a milestone** |
| `docs/STACK.md` | Every library and provider, and why each was chosen |
| `docs/ARCHITECTURE.md` | Components, data flow, service responsibilities |
| `docs/WORKFLOW.md` | User journey + indexing pipeline, step by step |
| `docs/DATA_MODEL.md` | Database schema and core entities |
| `docs/FEATURES.md` | Per-feature spec + V2 notes |
| `docs/SETUP.md` | Local dev: services, env vars, first-run commands |
| `docs/DEPLOYMENT.md` | AWS deployment target (phased) |
| `docs/LEARNING_LOG.md` | Interview-ready log, one entry per milestone |
| `docs/CONCEPTS.md` | Personal glossary of concepts new to me |
| `docs/SESSION_LOG.md` | Session-to-session handoff (written by `/endsession`) |