# Setup (local development)

Docker is the baseline for everything stateful. App code can run in containers or on the
host during dev — choose per component.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ with `uv`  — backend
- Node 20+ with pnpm — frontend
- A GitHub OAuth app (Client ID + Secret)
- A Google Gemini API key — used for both chat and embeddings. Free tier is enough;
  get one at <https://aistudio.google.com/apikey>. Note the free-tier limits (roughly
  10 RPM / 250 RPD on `gemini-2.5-flash`) — they're low enough to shape how you test.

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
# ai — one provider, both uses; read only by core/ai
GEMINI_API_KEY=
GEMINI_CHAT_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIMENSIONS=1536   # must match chunk.embedding's vector(N); changing it = full re-embed
# app
SESSION_SECRET=
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

# retrieval eval (the scoreboard — see RETRIEVAL.md)
docker compose exec worker python -m eval.seed           # clone + index the 3 pinned repos
docker compose exec worker python -m eval.seed --force   # re-seed; MANDATORY after any
                                                         # chunker or embedding change
docker compose exec worker python -m eval.run            # recall@5 / recall@20 scoreboard
```

The eval runs in `worker`, not `api` — it needs `git`, the DB, and the `.env` file, and
`worker` is the only service with all three. Chunks and embeddings are built at *index*
time, not query time, so changing how either works means re-seeding before the numbers mean
anything.

## Notes

- The worker needs `git` and the `CLONE_STORAGE_DIR` volume mounted.
- Long indexing jobs run in the worker only — never block an API request.
- One venv for the whole backend (`api`, `worker`, `core`, `indexer` share it).
