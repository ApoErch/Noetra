# Setup (local development)

Docker is the baseline for everything stateful. App code can run in containers or on the
host during dev — choose per component.

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ with `uv`  — backend
- Node 20+ with pnpm — frontend
- A GitHub OAuth app (Client ID + Secret)
- API keys: Anthropic (chat/summaries); embedding provider TBD

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
# ai
ANTHROPIC_API_KEY=
EMBEDDING_PROVIDER=            # TBD — set when the model is chosen
EMBEDDING_API_KEY=
# app
SESSION_SECRET=
TOKEN_ENCRYPTION_KEY=          # Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CLONE_STORAGE_DIR=/data/repos  # where the worker clones repos
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
```

## Notes

- The worker needs `git` and the `CLONE_STORAGE_DIR` volume mounted.
- Long indexing jobs run in the worker only — never block an API request.
- One venv for the whole backend (`api`, `worker`, `core`, `indexer` share it).
