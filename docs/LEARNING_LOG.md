# Learning Log

One entry per milestone (see `CLAUDE.md` build order). Written to be read back before
an interview — each entry should stand alone without needing the rest of the codebase
open.

**Entry format:**

```
## Milestone N — <name>

**Built:** one line, what exists now that didn't before.

**Core concept(s):** the 1-3 ideas worth being able to name and explain.

**Recruiter-ready explanation:** 2-3 sentences, plain language, no jargon left
unexplained. Should answer "walk me through how X works" on its own.

**Tricky part (optional):** anything that took real thought, worth remembering *why*
it was non-obvious.
```

---

## Milestone 1 — Skeleton

**Built:** Monorepo scaffolded — `/backend` (uv-managed, non-packaged app with `api`,
`worker`, `core`, `indexer`) and `/web` (Vite + React + TS + Tailwind). Docker Compose
brings up `db` (Postgres/pgvector), `redis`, `api` (FastAPI), and `worker` (Celery), all
verified healthy. `core/db.py` wired to Postgres and proven end-to-end via a real
Alembic migration (`alembic_version` row confirmed in the running container).

**Core concept(s):** uv "app layout" (`package = false`) vs. an installable library —
`api`/`worker`/`core`/`indexer` are plain importable packages that resolve because
`uvicorn`/`celery`/`alembic` insert the working directory onto `sys.path` at
invocation time, not because anything is `pip install -e`'d. Docker layer caching
(`COPY pyproject.toml` → `uv sync` → `COPY . .`) so source edits don't invalidate the
dependency-install layer. Alembic's revision chain (`down_revision` linking migrations
like git commits) and its `--autogenerate` diff of live DB state vs. `Base.metadata`.

**Recruiter-ready explanation:** The backend is one Python project split into four
packages that all share a single virtual environment: `api` (the FastAPI request
layer), `worker` (a Celery background-job runner), `core` (shared DB models, config,
and — later — the AI/retrieval logic), and `indexer` (pure code-parsing logic with no
DB or HTTP dependencies, so it's unit-testable on its own). The `api`/`worker` split
exists because indexing a repository — cloning it, parsing every file, generating
embeddings — can take minutes, and an HTTP request thread can't sit around waiting
that long; instead, the API just enqueues a job and returns immediately, while a
separate worker process picks it up from a queue (Redis) and works through it at its
own pace, updating a status field the frontend can poll. Everything runs in Docker
Compose locally exactly as it would in a small production deployment — Postgres,
Redis, the API container, and the worker container — so there's no "works on my
machine" gap between dev and prod. Schema changes are written as small, versioned
Python files (Alembic migrations) instead of someone hand-running SQL against the
database; each file says how to move the schema forward and how to undo it, and the
database itself tracks which one it's currently on, so any teammate — or CI, or
production — can run the same command and land on an identical schema.

**Tricky part:** Bind-mounting `./backend:/backend` into both `api` and `worker` for
live-reload also mounted the host's own `.venv` (built with Windows binaries) into
both Linux containers, masking the image's built venv. Both containers then tried to
delete-and-rebuild `.venv` concurrently on startup, and `worker` lost the race
(`Directory not empty`). Fixed by adding `/backend/.venv` as an anonymous volume on
both services — Docker overlays the more specific mount path on top of the bind mount,
so each container keeps its own private, Linux-native venv instead of sharing the
host's.

---

<!-- Add new entries above this line, most recent last -->
