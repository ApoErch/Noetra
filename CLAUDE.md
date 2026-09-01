See @README.md for project overview

# Workflow

- Read the newest entry in @docs/SESSION_LOG.md to pick up where the last session left off.
- Explain your reasoning, not just your conclusion. When you decide we need
something (e.g. "add a session secret"), first tell me *how you got there and why* —
your approach to the problem — then implement.
- Keep explanations tight: simple words + one small mock data example or analogy to help me understand the core concept; 
- Name the pattern. For example,  "this is reciprocal rank fusion", so I can
look it up; give the real trade-off (what else we could do, why this wins); link the
**official** docs. Idea + reason + example + link — no walls of text.
- Use the correct, industry-standard approach for each area (auth, DB access, API design, error handling),
and inform me when something *is* the standard.
- Don't over-engineer. No extra abstraction, defensive layers, or speculative generality I didn't ask for.
- When you finish an area, finish it cleanly: no dead code, stubs, or half-wired leftovers.
- Before running any command, state it, say what it does and why, then wait for my go-ahead. Never explain-then-run in the same turn. Only exception to this is when using Fetch or Web search tools.

## V1 scope

1. GitHub OAuth login
2. Repo import: public URL (plain clone) or private repo (clone with the user's decrypted token)
3. Background indexing pipeline: (clone+lexical index → parse+symbols → graph → chunk → embed → metrics)
4. Agentic RAG: three retrieval legs (lexical + semantic, RRF-fused, plus graph
traversal) driven by an agent that picks its own strategy per query. Also a **retrieval eval
set** that keeps all three honest
5. Streamed LangGraph chat agent with citations
6. basic dashboard (files, functions, LOC, language breakdown, largest files).
Languages at launch: Python, JavaScript, TypeScript. Nothing else.

## Code style

- @backend/api — FastAPI: auth, REST, retrieval + agent endpoints. **HTTP boundary
  only; no slow work inline.**
- @backend/worker — Celery tasks: the indexing pipeline. **All slow / repo-touching
  work lives here.**
- @backend/core — shared: DB models, schemas, GitHub client, `ai` + embeddings,
  retrieval, agent graph.
- @backend/indexer — pure parsing (Tree-sitter, AST chunking, symbol table, dep graph).**No DB, no HTTP** in order the indexer stays unit-testable in isolation.
- `api` and `worker` import `core` and `indexer`; `indexer` imports neither.
- Retrieval lives **only** in `core/retrieval`.
- The AI SDK is imported **only** in `core/ai` — no exceptions, that seam is the entire cost of swapping providers later.
- Strict typing both sides (mypy / TS strict). REST under `/api/v1`; validate with Pydantic at the boundary.
- Anything touching a repo is a Celery task in `worker` — never inline in `api`.
- Secrets via env only; keep `.env.example` current.
- Everything scoped by `repository_id`; ownership checked on every read.

## Additional Instructions

- Agentic RAG design — the core engineering: @docs/RETRIEVAL.md
- Milestones, current status, ordering rationale: @docs/BUILD_ORDER.md
- Every library and provider, and why each was chosen: @docs/STACK.md
- Components, data flow, service responsibilities: @docs/ARCHITECTURE.md
- User journey and indexing pipeline, step by step: @docs/WORKFLOW.md
- Database schema and core entities: @docs/DATA_MODEL.md
- Per-feature spec: @docs/FEATURES.md
- Local dev such as services, env vars, first-run commands: @docs/SETUP.md
- AWS deployment target (phased): @docs/DEPLOYMENT.md
- Session-to-session handoff (written by `/endsession`): @docs/SESSION_LOG.md