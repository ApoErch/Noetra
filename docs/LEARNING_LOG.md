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

## Milestone 2 — Auth

**Built:** Full GitHub OAuth login flow, verified end-to-end against real GitHub: a
dev OAuth App, `core/github.py` (authorize-URL builder, code→token exchange, profile
fetch), `core/security.py` (Fernet encryption for the stored token), `api/auth.py`
(`/login`, `/callback`, `/logout`, `/me`, plus a reusable `get_current_user`
dependency), and signed cookie sessions via Starlette's `SessionMiddleware`. Confirmed
a real `User` row lands in Postgres with a genuinely encrypted `access_token`.

**Core concept(s):** the OAuth2 Authorization Code flow (redirect → user approves on
GitHub → one-time `code` → server-side exchange for a token — the code never touches
the browser's URL bar for anything sensitive). CSRF protection via a random `state`
value round-tripped through the session. Stateless signed-cookie sessions
(`itsdangerous`) instead of a server-side session table — the session data lives in
the client's cookie, tamper-evident via a signature, so `api` stays stateless.
Symmetric encryption (Fernet/AES) for a *reversible* secret (need the token back to
call GitHub later) as distinct from password hashing (one-way, never need it back).

**Recruiter-ready explanation:** Logging in redirects the user to GitHub, which asks
them to approve access and sends them back with a short-lived, single-use code. The
backend exchanges that code server-to-server (using a client secret only the backend
knows) for a real access token — the token itself never appears in the browser or the
URL. That token is encrypted before it's stored in Postgres, because it's a live
credential that can act on the user's GitHub account; if the database ever leaked, an
unencrypted token would be as dangerous as a leaked password, except you can't hash it
the way you hash passwords, since the app needs the real value back later to call
GitHub's API. Once logged in, "being logged in" is represented by a small signed
cookie holding the user's ID rather than a row in a sessions table — the server can
trust the cookie wasn't tampered with because it's cryptographically signed, so no
extra database lookup or shared session store is needed on every request.

**Tricky part:** GitHub's REST API silently returns `503` (not a clear `401`/`403`)
for authenticated requests missing a `User-Agent` header — traced by comparing an
unauthenticated request (worked, `401`) against the authenticated one (`503`) from
inside the running container, then confirming against GitHub's own REST API docs that
`User-Agent` is a hard requirement on every request. Also hit the classic-OAuth-App
limitation that private-repo access has no read-only scope — only the broad `repo`
scope exists, which grants read+write; true read-only permissions require a GitHub
App instead, a bigger integration change deferred rather than taken on here.

---

<!-- Add new entries above this line, most recent last -->
