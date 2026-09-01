# Stack

What we use and why. Versions are pinned in `backend/pyproject.toml` and `web/package.json`
— read those for exact numbers; this doc is the reasoning.

## Frontend

| Piece | Why |
|---|---|
| **React + TypeScript** | Strict mode both sides. Contracts validated at the boundary, never hand-maintained twice. |
| **Vite** | Dev server + build. |
| **Tailwind** | Always-dark theme, one deliberate palette — no light/dark split to maintain. |
| **Monaco** | The VS Code editor. Read-only viewer with decorations for cited line ranges, which is what makes a citation *clickable* rather than just printed. Types are hand-rolled to avoid an ~80 MB devDependency. |
| **TanStack Query** | Server-state cache: polling repo status during indexing, request dedup, no hand-written loading/error state. |

## Backend

| Piece | Why |
|---|---|
| **FastAPI + Pydantic v2** | REST under `/api/v1`, validated at the HTTP boundary. Free OpenAPI schema for frontend type generation. |
| **SQLAlchemy 2.0** | Typed ORM. Models in `core/models.py`. |
| **Alembic** | Migrations as a revision chain. **Always read a generated migration before applying it** — autogenerate can't see hand-written raw-SQL indexes and will propose dropping them. |
| **Celery + Redis** | Redis is both the Celery broker and the per-repo index lock. Anything touching a repo is a Celery task — never inline in `api`. |
| **Postgres + pgvector** | Single source of truth. pgvector keeps embeddings in the same DB, so there's no second datastore in V1. |
| **Tree-sitter** | Language-agnostic parser (one API, many grammars) producing a concrete syntax tree — vs. a per-language tool like Python's `ast`. Grammars: python, javascript, typescript (the last ships two languages, one for `.ts` and one for `.tsx`). |
| **LangGraph** | Runs the agent's state machine. We write the graph; it executes it. See `RETRIEVAL.md`. |
| **uv** | One venv for the whole backend. App layout (`package = false`) — `api`/`worker`/`core`/`indexer` are plain importable packages, not installed distributions. |

## AI provider — Google Gemini (embeddings), switchable (chat)

**Embeddings stay Gemini-only** — Anthropic has no embedding API at all, and switching
embedding providers means a full re-embed anyway, so there's no live-switch case for it.
**Chat is provider-switchable** via `core/ai/chat.py::get_chat_model(provider)`, a factory
returning a ready LangChain chat model for `"gemini"` / `"openai"` / `"anthropic"` — useful
for testing the M8 agent loop against a different model. Nothing outside `core/ai` imports
any provider's SDK directly; that's the seam, not a promise every provider ships equally
tested — Gemini is the one actually exercised end-to-end.

| | Model | Notes |
|---|---|---|
| Chat (default) | `gemini-3.5-flash` | Agent loop. Free tier. `default_chat_provider` config. |
| Chat (alt.) | `gpt-4o-mini` / `claude-sonnet-5` | Via `get_chat_model("openai"\|"anthropic")`. |
| Embeddings | `gemini-embedding-001` | **1536 dims** (truncated from its 3072 default). |

**Why 1536 and not 3072:** pgvector's HNSW index caps the `vector` type at 2000 dimensions —
3072 would force the `halfvec` type. 1536 is a Google-recommended Matryoshka size and keeps
the column on the well-trodden path.

Three provider details that are easy to get wrong and are `core/ai`'s job to absorb:

- **`gemini-embedding-001` only pre-normalizes at 3072.** At 1536 we L2-normalize
  client-side or cosine distance is silently wrong — degraded recall, no error.
- **Input caps at 2048 tokens** (~8 KB). Some leaf-entity chunks exceed it; truncate first.
- **`task_type` is asymmetric.** `RETRIEVAL_DOCUMENT` when indexing, `CODE_RETRIEVAL_QUERY`
  when querying. Using one for both sides is a quiet mistake.

### Free-tier limits are a design constraint

`gemini-3.5-flash` is roughly **15 RPM / 1,500 RPD** and one agent turn is 3–6 model calls.
`gemini-embedding-001`'s free tier is separately capped at **100 requests/minute *and*
1,000 requests/day** — both measured live in M6 by hitting them, not from published docs.
That shapes architecture, not just testing:

- The agent eval (~200 calls for a full pass) **must checkpoint per question and resume**.
- The embedding stage must batch, rate-limit, and back off on 429 — and `chunk.embedding` is
  **nullable** precisely so it can select `WHERE embedding IS NULL` and resume rather than
  re-embed a whole repo.
- The **daily** cap is the one worth respecting, not just the per-minute one: it doesn't
  recover in the wall-clock time a retry loop would wait, and Google's `retryDelay` hint
  looks the same (a few seconds) whether it's the per-minute or the daily quota that's
  actually exhausted — the two are only distinguishable by reading the error's `quotaId`.

Get a key at <https://aistudio.google.com/apikey>. Env vars in `.env.example`; see `SETUP.md`.

## Infrastructure

- **Docker Compose** locally: `db` (`pgvector/pgvector:pg16`), `redis`, `api` (uvicorn),
  `worker` (celery). `api` and `worker` build from the same image with different entrypoints.
- **GitHub Actions** CI.
- **AWS** deploy target — phased, see `DEPLOYMENT.md`.

Coding conventions that apply across all of this (strict typing, secrets via env, scoping by
`repository_id`) live in `CLAUDE.md` — that's the authority, don't duplicate them here.
