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

## AI provider — OpenAI (paid), switchable

Nothing outside `core/ai` imports a provider SDK. Two switch points, both config:

- **Embeddings:** `EMBEDDING_PROVIDER` (only `openai` is implemented) →
  `core/ai/embeddings.py::embed_documents / embed_query`. Single-provider on purpose:
  switching embedding models is a full re-embed regardless (a different model is a different
  vector space), so a live switch has no use case — a new provider is one more branch.
- **Chat:** `DEFAULT_CHAT_PROVIDER` (`openai` | `anthropic`) →
  `core/ai/chat.py::get_chat_model(provider)`, a factory returning a ready LangChain chat
  model — useful for testing the M7 agent loop against a different model.

| | Model | Notes |
|---|---|---|
| Embeddings | `text-embedding-3-small` | **1536 dims natively**; vectors arrive unit-normalized; ~8k-token input cap (the API rejects over-long input, so `core/ai` truncates first). ~$0.02 / M tokens. |
| Chat (default) | `gpt-4o-mini` | Agent loop. |
| Chat (alt.) | `claude-sonnet-5` | Via `get_chat_model("anthropic")`. |

**Why OpenAI, why paid:** the semantic leg was first built on Gemini's free tier, whose
per-minute *and* per-day quotas made the M6 measurement impossible and pulled real engineering
(pacing, token-budget batching, retry tuning) into a feature that a cent's worth of tokens
makes unnecessary. All of that was deleted, not parameterised — see `CONCEPTS.md` A20/B18.
Rate limits are not a design constraint on a paid tier; if 429s ever appear, the embedding
page size (`worker/embedding.py`) is the one knob.

1536 is also under pgvector's 2000-dim cap for indexing the plain `vector` type, should an
ANN index ever be added (none in V1 — see `DATA_MODEL.md`).

Get a key at <https://platform.openai.com/api-keys>. Env vars in `.env.example`; see `SETUP.md`.

## Infrastructure

- **Docker Compose** locally: `db` (`pgvector/pgvector:pg16`), `redis`, `api` (uvicorn),
  `worker` (celery). `api` and `worker` build from the same image with different entrypoints.
- **GitHub Actions** CI.
- **AWS** — one EC2 host running this same Compose stack, every resource (VPC, instance,
  EBS, EIP, IAM role, Route 53, SSM secrets) declared in **Terraform** — infra as reviewable,
  reproducible code in the repo rather than console clicks. See `DEPLOYMENT.md`.

Coding conventions that apply across all of this (strict typing, secrets via env, scoping by
`repository_id`) live in `CLAUDE.md` — that's the authority, don't duplicate them here.
