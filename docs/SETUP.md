# Setup (local development)

Docker is the baseline for everything stateful. App code can run in containers or on the
host during dev — choose per component.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ with `uv`  — backend
- Node 20+ with pnpm — frontend
- A GitHub OAuth app (Client ID + Secret)
- An OpenAI API key — used for both embeddings and chat. Get one at
  <https://platform.openai.com/api-keys>. Embedding the eval repos costs cents.

## Services (docker-compose)

Compose owns the stateful infra so local mirrors production shape:

| service | image | purpose |
|---------|-------|---------|
| `db` | `pgvector/pgvector:pg16` | Postgres with pgvector preinstalled |
| `redis` | `redis:7` | Celery broker + task status |
| `api` | built from `/backend` | FastAPI (`/api/v1`), hosts the agent endpoint |
| `worker` | built from `/backend` | Celery worker — the indexing pipeline |
| `web` | built from `/web` | React dev server (or run on host) |

`api` and `worker` build from the same image, different entrypoints (uvicorn vs celery).

## Environment

Copy `.env.example` → `.env`. Keep `.env.example` current whenever a var is added.

```
# database
DATABASE_URL=postgresql+psycopg://noetra:noetra@db:5432/noetra
# redis / celery
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
# github oauth
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_OAUTH_CALLBACK=http://localhost:8000/api/v1/auth/callback
FRONTEND_URL=http://localhost:5173
# ai — read only by core/ai. Embeddings + chat default to OpenAI; one key covers both.
EMBEDDING_PROVIDER=openai          # only "openai" is implemented; the switch point for a future provider
OPENAI_API_KEY=
OPENAI_EMBEDDING_MODEL=text-embedding-3-small   # 1536 dims — must match chunk.embedding vector(N); changing model = full re-embed
OPENAI_CHAT_MODEL=gpt-5.4-mini
AGENT_TOOL_BUDGET=8                # max tool calls per chat question
AGENT_REPO_MAP_TOKENS=1500         # token budget for the repo map in the agent prompt
DEFAULT_CHAT_PROVIDER=openai       # openai | anthropic
ANTHROPIC_API_KEY=                 # only if testing the chat agent against Anthropic
ANTHROPIC_CHAT_MODEL=claude-sonnet-5
# langsmith tracing (optional; see "Tracing" below)
LANGSMITH_TRACING=false
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=noetra
# app
SESSION_SECRET=
SESSION_MAX_AGE_SECONDS=1209600  # 14 days; the session cookie is signed, not server-side, so
                                 # this is the real upper bound on a stolen cookie's life
SESSION_HTTPS_ONLY=false         # MUST be true anywhere the app is served over TLS
TOKEN_ENCRYPTION_KEY=          # Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CLONE_STORAGE_DIR=/data/repos  # where the worker clones repos
# eval harness — a GitHub token that can read the private noetra repo (public eval repos need no auth)
EVAL_GITHUB_TOKEN=
```

## First run

```bash
# 1. infra + services
docker compose up -d db redis
docker compose up --build api worker

# 2. database schema (Alembic migrations)
docker compose exec api alembic upgrade head

# 3. frontend (host)
cd web && pnpm install && pnpm dev
```

- API: http://localhost:8000  (OpenAPI docs at `/docs`)
- Web: http://localhost:5173

## Frontend types

Generate TypeScript types from the FastAPI OpenAPI schema (e.g. `openapi-typescript`)
so frontend and backend contracts stay in sync. Never hand-maintain DTOs on both sides.

## Common commands

```bash
docker compose logs -f worker          # watch the indexing pipeline
docker compose exec api alembic revision --autogenerate -m "msg"
docker compose exec db psql -U noetra   # inspect the DB
docker compose down -v                  # reset everything (drops volumes)

# retrieval eval (the scoreboard — see RETRIEVAL.md). Default repo is noetra.
docker compose exec worker python -m eval.seed             # clone + index + embed noetra
docker compose exec worker python -m eval.seed --force     # re-seed; MANDATORY after any
                                                           # chunker or embedding change
docker compose exec worker python -m eval.seed --no-embed  # skip embedding — for
                                                           # chunker-only iteration
docker compose exec worker python -m eval.seed --repos all # all 3 pinned repos
docker compose exec worker python -m eval.run              # recall@5 / recall@20 scoreboard
docker compose exec worker python -m eval.run --legs lexical            # ablate a leg out
docker compose exec worker python -m eval.run --legs lexical,semantic   # explicit, both
docker compose exec worker python -m eval.run --repos noetra,requests   # more repos

# agent eval (runs the real chat agent; ~1 cent per question; checkpointed per question)
docker compose exec worker python -m eval.agent                       # baseline → eval/out/agent-baseline.jsonl
docker compose exec worker python -m eval.agent --tag x --no-repo-map # ablation: no repo map
docker compose exec worker python -m eval.agent --kinds graph --repos all      # the M8 bucket
docker compose exec worker python -m eval.agent --kinds graph --no-graph-tools # ablation: no find_references
docker compose exec worker python -m eval.agent --tag y --model gpt-4.1-mini   # model A/B
docker compose exec worker python -m eval.agent --limit 3 --fresh     # smoke test, 3 questions

# repo metrics (M9) — written by the pipeline, so there is no command to recompute them:
# re-import the repo, or hit Resume on one parked at embedding/metrics.

# unit tests (pure — no DB, no LLM)
cd backend && uv run pytest -q
```

The eval runs in `worker`, not `api` — it needs `git`, the DB, and the `.env` file, and
`worker` is the only service with all three. Chunks and embeddings are built at *index*
time, not query time, so changing how either works means re-seeding before the numbers mean
anything. Embedding is resumable (`WHERE embedding IS NULL`), so an interrupted seed
continues on the next run; `--no-embed` skips it when only the chunker changed.

## Tracing (LangSmith)

Set `LANGSMITH_TRACING=true` plus the key and project in `.env`, then
`docker compose up -d --force-recreate api worker` (a restart does not re-read `.env`).
Nothing else: `langchain-core` picks the variables up and records every chat turn as one
trace named `agent_turn` — each `call_model`, each tool call with its arguments and result,
token usage, and latency — tagged with `repository_id` in the metadata. The eval
(`eval.agent`) is traced the same way, so a bad answer in the scoreboard can be opened and
read step by step. Off by default; it adds a network call per span.

## Sessions

Login state is a **signed cookie**, not a server-side record: the whole session is
`{"user_id": ...}`, signed with `SESSION_SECRET`. Logging out clears it, and Starlette sends
back an expired cookie so the browser drops it.

The consequence worth knowing: logout can only discard *that browser's* copy. There is no
server-side store to revoke against, so a cookie captured beforehand stays valid until
`SESSION_MAX_AGE_SECONDS` elapses. Closing that gap means a `session_version` column on
`users`, bumped on logout and checked in `get_current_user` — deliberately not built in V1.
Logout also does not revoke the stored GitHub token (imports must keep working) and cannot
log the user out of github.com (OAuth, unlike OIDC, has no logout endpoint).

Every session option is now passed explicitly in `api/main.py` rather than inherited from
Starlette's defaults, because `https_only` is exactly the kind of setting that must not be
silently `False` in production.

## Notes

- The worker needs `git` and the `CLONE_STORAGE_DIR` volume mounted.
- Long indexing jobs run in the worker only — never block an API request.
- One venv for the whole backend (`api`, `worker`, `core`, `indexer` share it).
