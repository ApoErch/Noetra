# Concepts — personal glossary

Plain-language notes on tools/patterns that were new to me when they first showed up in
this project. Not recruiter-facing (that's `LEARNING_LOG.md`) — this is just for me to
look things up later without re-deriving them.

---

## Fernet encryption (from the Python `cryptography` package)

**What it is:** a way to scramble text (encrypt) using a secret key, and unscramble it
(decrypt) later using that same key. "Symmetric" just means the same key does both
directions — unlike password hashing, which only ever goes one way.

**Why we need it here:** we store a user's real GitHub access token in the database
(so the backend can call the GitHub API on their behalf later — e.g. to clone a private
repo). If the database ever leaked, a token stored in plain text would let an attacker
act as that user on GitHub. Since we need the *original* token back (not just to check
if it matches, like a password), hashing doesn't work — hashing is one-way. Encryption
is the right tool because it's reversible with the key.

**How it's used in Noetra:**
- `core/security.py` holds `encrypt_token()` / `decrypt_token()`, built on
  `cryptography.fernet.Fernet`.
- The key comes from an env var, never hardcoded or committed.
- `core/github.py` (the OAuth flow) calls `encrypt_token()` right before saving a user's
  access token to the DB, and `decrypt_token()` whenever the token needs to be used
  again (e.g. cloning a private repo).

**Is this standard?** Yes — any app that stores third-party OAuth tokens (GitHub,
Google, Slack integrations, etc.) encrypts them at rest. It's a different threat model
from password storage: passwords are hashed (never need the original back), tokens are
encrypted (the original value is needed again to make API calls).

**Docs:** [`cryptography` Fernet docs](https://cryptography.io/en/latest/fernet/)

---

## `uv` and `.venv` — why both exist

**The confusion:** if I'm using `uv`, why is there still a `.venv` folder? Isn't `uv`
supposed to replace that?

**The actual relationship:** `uv` is not an alternative *to* virtual environments — it's
a faster replacement for the *tooling* that manages one (`pip` + `venv` + `poetry`, all
in one Rust binary). Python still needs an isolated folder containing its own interpreter
reference and installed packages, so this project's dependency versions don't collide
with other Python projects or the system Python. That isolated folder is the `.venv` —
same concept as always, just created and kept in sync automatically instead of by hand.

**What changes day to day:**
- Old way: `python -m venv .venv`, activate it, `pip install -r requirements.txt`,
  manually keep a lockfile in sync.
- `uv` way: `uv add <package>` does all of that in one step — updates `pyproject.toml`,
  resolves a lockfile, creates `.venv` if it's missing, installs into it. Much faster
  due to a global cache and Rust implementation.

**Gotcha hit in this project:** a `.venv` created *inside the Linux container* (via
`docker compose exec` before the anonymous-volume isolation was set up in
`docker-compose.yml`) leaked onto the Windows host through the bind mount. Linux venvs
contain symlinks (e.g. `lib64 -> lib`) that Windows tools can't cleanly modify, which
broke `uv add` until that stale `.venv` was deleted and regenerated natively on Windows.
Lesson: the host `.venv` and the container's `.venv` are meant to be entirely separate
(that's what the `- /backend/.venv` anonymous volume line in `docker-compose.yml`
enforces) — one is for host tooling (mypy, ruff, `uv add`), the other is what actually
runs inside the container.

**Docs:** [uv docs — projects](https://docs.astral.sh/uv/guides/projects/)

---

## OAuth App `repo` scope has no read-only option

**The gotcha:** GitHub's classic OAuth Apps (what Noetra registered) only offer the
`repo` scope for accessing private repository code, and per GitHub's own docs that
scope always grants "full access... including read **and write**." There is no
`repo:read`-style narrower scope for OAuth Apps — the only other repo-related scopes
(`repo:status`, `repo_deployment`, `repo:invite`) cover things like commit statuses and
deployments, not repository contents at all.

**Why it can't be narrowed here:** true read-only access (e.g. a "Contents: Read-only"
permission) only exists on **GitHub Apps**, which use fine-grained permissions instead
of scopes. GitHub Apps are a different integration model entirely — different
registration flow, different install step, and installation tokens instead of a simple
OAuth bearer token. Switching would mean redesigning the auth milestone, not editing a
scope string.

**The decision for V1:** keep the OAuth App and the `repo` scope, accept that the
stored token is *more privileged than the app uses*. This is safe in practice only
because Noetra's V1 scope is read-only end to end (clone, parse, index, chat, search,
metrics) and no write-capable endpoints (commit, push, PR comment, etc.) are planned —
so the token's write capability is never exercised by any code path, even though it
technically exists. If a write feature is ever added later, this decision needs
revisiting (likely: migrate to a GitHub App at that point).

**Why this matters / interview framing:** this is a "principle of least privilege" gap
at the *credential* level (the token can do more than the app does), separate from
whether the *app's behavior* is safe. It's a known, documented platform limitation of
OAuth Apps vs. GitHub Apps — worth naming explicitly rather than treating as an
oversight.

**Docs:** [OAuth Apps: scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) · [GitHub Apps vs OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)

---

## Signed session cookies (Starlette `SessionMiddleware` / `itsdangerous`)

**What it is:** after login, the server needs a way to recognize "this request came
from the same browser that just logged in" — HTTP itself has no memory between
requests. The fix: the server puts a small dict (just `{"user_id": "<uuid>"}`) in a
cookie, and *signs* it with a secret (`session_secret`) so the browser can hold it and
send it back, but can't tamper with it undetected.

**Signed, not encrypted — the distinction matters:** signing proves the cookie wasn't
edited (any change breaks the signature check); it does *not* hide the contents from
the browser. That's fine here because `user_id` isn't sensitive — you already know your
own ID. Contrast with the GitHub access token (see Fernet entry above), which *is*
encrypted, because that value must stay hidden even from the browser holding the
session.

**Why we need it here:** without it, every request would need to redo the full GitHub
OAuth flow to prove identity. The signed cookie is the "remember me" mechanism between
login and logout.

**How it's used in Noetra:**
- `SessionMiddleware` is registered in `backend/api/main.py` with
  `secret_key=settings.session_secret`.
- `auth.py`'s callback handler sets `request.session["user_id"] = str(user.id)` after a
  successful GitHub login.
- `get_current_user` (a FastAPI dependency) reads `request.session["user_id"]` back out
  on every subsequent request, loads the matching `User` row from Postgres, and 401s if
  it's missing.
- Logout is just `request.session.clear()`.

**Is this standard?** Yes — this is the standard signed-cookie session pattern, the
same family of idea as JWTs (also signed-not-encrypted) just a different format/library.
Common in small-to-mid apps that don't need a server-side session store (Redis, DB
table) — the cookie itself carries the state.

**Docs:** [Starlette SessionMiddleware](https://www.starlette.io/middleware/#sessionmiddleware) · [itsdangerous](https://itsdangerous.palletsprojects.com/)

---

## Docker Compose service networking (why `db` resolves in a container but not on the host)

**What it is:** Docker Compose puts all services (`db`, `redis`, `api`, `worker`) on a
private virtual network and gives each one a DNS nickname equal to its service name in
`docker-compose.yml`. Any container on that network can reach Postgres by just saying
`db`, the same way you'd normally need an IP address or `localhost`.

**Why it matters here:** `.env`'s `DATABASE_URL` is `postgresql+psycopg://noetra:noetra@db:5432/noetra`
— that `db` only resolves *inside* the Docker network. Running `alembic upgrade head`
directly on Windows (outside any container) fails immediately, because the host has no
idea what `db` means. That's why every DB/Celery management command in this project runs
via `docker compose exec <service> ...` — the command executes *from inside* a container
that's already on the network, so the nickname resolves.

**Is this standard?** Yes — this is how every multi-container Compose (or Kubernetes)
setup works; service discovery by name instead of hardcoded IPs is the whole point of
container networking.

**Docs:** [Compose networking](https://docs.docker.com/compose/how-tos/networking/)

---

## SQLAlchemy `Base.metadata` registration + Alembic autogenerate

**The confusion:** `migrations/env.py` has a line `from core import models  # noqa: F401`
that's never referenced again — looks like dead code, but deleting it silently breaks
migrations.

**What's actually happening:** every model class (`User`, `Repository`) registers itself
into a shared registry (`Base.metadata`) purely as a side effect of its class body being
*executed* — i.e., of the file being imported. `core/db.py` (which defines `Base`) never
imports `models.py`, so nothing forces that registration to happen — unless something
else explicitly imports it. That's the whole job of that one line in `env.py`: force
Python to read `models.py` so both classes register themselves before Alembic compares
"what's in Postgres right now" against "what `Base.metadata` says should exist" and
generates a migration for the difference.

**Consequence of removing it:** `Base.metadata` would be empty when alembic runs, so
`--autogenerate` would see zero expected tables and generate a migration that **drops**
every real table — because as far as that process can tell, none of them should exist.

**Is this standard?** Yes — "import your models somewhere alembic's `env.py` can see
them" is the standard SQLAlchemy + Alembic setup; nothing custom to Noetra.

**Docs:** [Alembic autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)

---

## Celery producer/consumer split — `send_task` by name vs. importing the task

**What it is:** a Celery *producer* (code that enqueues work) doesn't need the actual
task function — just the broker connection and the task's registered *name* (a string).
`celery_app.send_task("worker.tasks.clone_repository", args=[...])` publishes a message
to Redis without ever importing `worker.tasks`. Only the *consumer* (the `worker`
process, which actually runs `@celery_app.task`-decorated functions) needs the real
implementation.

**Why it matters here:** `api/repos.py` needs to enqueue the clone job, but CLAUDE.md's
module boundary says `api` should never import `worker` (that's where all the slow,
repo-touching logic is supposed to live, isolated from the HTTP layer). `send_task` by
name is what makes that boundary possible — `api` only needs to agree on a task *name*
with `worker`, not share code with it. Proven this session: enqueueing a task that
didn't exist yet still worked (message published, worker received it, only rejected it
because the name wasn't registered on *its* side) — clean confirmation the two sides are
decoupled.

**Is this standard?** Yes — this is the standard way to keep a web API and a background
worker in separate deployable processes/images while still coordinating work between
them.

**Docs:** [Celery: calling tasks](https://docs.celeryq.dev/en/stable/userguide/calling.html)

---

## Git credential injection via `http.extraHeader` (vs. credentials-in-URL)

**The naive way:** `git clone https://{token}@github.com/owner/repo.git` — works, but
`git` writes that URL (token included) straight into the cloned repo's `.git/config` in
plaintext. Anything that can read the repo's files afterward can read a live GitHub
token.

**The better way:** pass the token as a one-off HTTP auth header via a `-c` flag, which
applies only to that single command and is never persisted to disk:
`git -c http.extraHeader="AUTHORIZATION: basic {base64(x-access-token:{token})}" clone https://github.com/owner/repo.git`

**Why we need it here:** every clone in Noetra will be authenticated with the owning
user's real GitHub OAuth token (see the `is_private`-field decision in the 2026-07-21
session log entry — auth is unconditional, not just for private repos), so avoiding
token leakage into the cloned repo's own config file matters for every single import,
not just private ones.

**Is this standard?** Yes — this is the same mechanism GitHub's own `actions/checkout`
action uses internally to authenticate clones in CI without leaving a token behind in
the checked-out repo.

**Docs:** [git-config: `http.extraHeader`](https://git-scm.com/docs/git-config#Documentation/git-config.txt-httpextraHeader)

---

## Correction: `git clone -c ...` is *not* purely process-local

**The earlier claim (now known wrong):** the entry above says a `-c` flag "applies only to that single command and is never persisted to disk." That's true for most git commands, but **not** for `git clone`. Verified by hand this session: after `git clone -c http.extraHeader="Authorization: Basic ..." <url> dest`, `cat dest/.git/config` showed the header sitting right there under `[http]` — same leak as putting the token in the URL, just reached a different way.

**Why `git clone` is special:** cloning has to persist *some* settings into the new repo's config anyway (the remote URL, the default branch tracking) so future `git fetch`/`git pull` work without re-specifying everything. Git's implementation carries certain `-c` overrides — including `http.*` ones — into that same saved config, on the assumption you'd want later fetches to keep using the same settings. Reasonable default for e.g. `http.postBuffer`, actively dangerous for a bearer credential.

**The real fix:** treat the clone as producing a config that needs a cleanup pass — run `git config --unset-all http.extraHeader` inside the destination immediately after a successful clone. The token still never touches the clone *URL* (so it's never in shell history / process argv longer than necessary) and now never survives on disk past the clone step either.

**Lesson generalized:** "`-c` is a one-off override" is a per-*command* guarantee, not a blanket git guarantee — worth verifying empirically (`cat .git/config` after the fact) rather than trusting the general rule for a specific command you haven't checked.

**Docs:** [git-clone docs](https://git-scm.com/docs/git-clone) (see the note on `-c`/`--config` under OPTIONS)

---

## GitHub has two different auth surfaces: REST API (`Bearer`) vs. git-over-HTTPS (`Basic`)

**The mistake:** authenticated `git clone` with `-c http.extraHeader="Authorization: Bearer <token>"` — reasonable guess, since `Bearer` is what `core/github.py` already uses successfully against `https://api.github.com`. It failed with `fatal: could not read Username for 'https://github.com'`, as if no credentials had been sent at all.

**What's actually going on:** `github.com`'s REST/GraphQL API and its git wire-protocol server (the thing `git clone`/`fetch`/`push` actually talk to) are different pieces of infrastructure with different, unrelated auth conventions. The git server predates the `Bearer` scheme's use here and only recognizes standard **HTTP Basic Authentication** — the same mechanism you'd get "for free" by embedding credentials in the clone URL (`https://<token>@github.com/...`); git converts that into a Basic header internally before sending it. A `Bearer` header is simply not a scheme the git server checks for, so it behaves as if the request were anonymous.

**How it's used in Noetra:** `worker/tasks.py` builds `Authorization: Basic <base64("x-access-token:" + token)>` for the clone specifically, while `core/github.py` keeps using `Authorization: Bearer <token>` for REST calls (`/user`) — same token, two different header formats, because the two servers being called expect different things.

**Is this standard?** Yes — this is a real, commonly-hit GitHub gotcha, not a Noetra-specific quirk. It's the same reason `git`'s own credential helpers and CI tools (e.g. `actions/checkout`) always construct Basic auth for clone operations regardless of what a REST client would use.

**Docs:** [Git Book — Basic Authentication over Smart HTTP](https://git-scm.com/book/en/v2/Git-on-the-Server-Smart-HTTP)

---

## `sys.path` isn't the same for every way of starting Python

**The confusion:** the `api` service could already `import core` and `import api` just fine (via `uv run uvicorn api.main:app`), so it seemed safe to assume the `worker` service (`uv run celery -A core.celery_app worker`) could too. It couldn't — it crashed on startup with `ModuleNotFoundError: No module named 'worker'`, despite the exact same working directory (`/backend`) and the exact same file actually being present on disk.

**What's actually going on:** Python decides what folders are importable (`sys.path`) partly based on *how* the process was started, not just the working directory. Running `python -c "..."` (or `python -m x`) auto-adds the current directory. But both `uvicorn` and `celery` here are started as **installed console-script entry points** (`.venv/bin/uvicorn`, `.venv/bin/celery`), and by default that does *not* add the working directory. `uvicorn` happens to special-case this — it explicitly inserts the cwd into `sys.path` itself when resolving a dotted app string like `api.main:app`, as a deliberate convenience feature. `celery` has no equivalent special-casing for its `autodiscover_tasks(["worker"])` call, so it just failed the plain `import worker`.

**The fix:** don't rely on one tool's incidental convenience behavior — set `PYTHONPATH=/backend` explicitly as an environment variable on both services in `docker-compose.yml`, so importability doesn't depend on which CLI tool happens to be generous about it.

**Is this standard?** Yes — `PYTHONPATH` is the standard, explicit way to guarantee a directory is importable regardless of entry point; relying on a specific tool's internal convenience logic (as the `api` service was doing, unknowingly) is fragile precisely because it isn't documented behavior you can count on from every tool.

**Docs:** [Python docs — `sys.path` initialization](https://docs.python.org/3/library/sys.path_init.html)

---

## App logout vs. IdP logout (federated / RP-initiated logout)

**The question that came up:** after clicking "log out" in Noetra, then "log in" again in the same browser, GitHub skipped straight past its login/consent screen and logged the user right back in. Shouldn't logout have logged them out of GitHub too?

**The distinction:** GitHub here is the **identity provider (IdP)** — the party that actually verifies who you are. Noetra is the **relying party (RP)** — it just trusts GitHub's answer. `request.session.clear()` (Noetra's logout) only ends the *local* session between the browser and Noetra. It has no effect on the browser's separate github.com login cookie, or on GitHub's record that you already consented to the "Noetra" OAuth app's scopes — both of those live entirely on GitHub's side.

**Why this is correct, not a bug:** if logging out of one small app also logged you out of github.com (and by extension every other app using "Sign in with GitHub" in that browser), that would be surprising and disruptive — the same reason logging out of a random site doesn't log you out of Gmail after "Sign in with Google." Local logout and IdP logout are supposed to be independent.

**Why there's no way to force it here:** plain OAuth 2.0 (what GitHub implements) is only an authorization protocol — it has no "logout" concept at all. **OpenID Connect** (an identity layer built on top of OAuth) adds an optional `end_session_endpoint` for exactly this, called RP-initiated logout / federated sign-out — but GitHub doesn't implement OIDC, so no such endpoint exists to call.

**The closest available thing (different, not equivalent):** GitHub does expose `DELETE /applications/{client_id}/token` to revoke the OAuth app's access token. That forces the consent screen to reappear next login (since access was revoked) — but it still doesn't touch the github.com session itself, and Noetra doesn't currently call it (would mean re-consenting on every login, not worth it for V1).

**Is this standard?** Yes — this is how essentially every "Sign in with X" integration behaves (Google, Facebook, GitHub, etc.).

**Docs:** [GitHub OAuth Apps — scopes & token revocation](https://docs.github.com/en/rest/apps/oauth-applications) · [OpenID Connect RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)

---

## IDOR (Insecure Direct Object Reference) and the 404-vs-403 trick

**What it is:** a very common web vulnerability class where an endpoint looks up a
resource by an ID from the request (`/repos/{id}/files`) without checking whether the
*current user* is actually allowed to see that ID. If the check is missing, any logged-in
user can read any other user's data just by changing the ID in the URL/request.

**Why we need it here:** `GET /repos/{id}/files` needs to return one user's repo data,
never another user's, even though both are just rows in the same `repositories` table
identified by UUID.

**How it's used in Noetra:** `_get_owned_repository()` (`backend/api/repos.py`) folds the
ownership check directly into the DB query — `WHERE id = :id AND user_id = :current_user`
— instead of "look up by id, then separately check the owner." If no row matches either
condition, it raises **404**, not 403. This is deliberate: a 403 ("forbidden") would leak
that the ID *exists*, just isn't yours — letting an attacker map out valid IDs even
without reading their contents. 404 makes "doesn't exist" and "exists but isn't yours"
look identical from the outside (GitHub itself does the same thing for private repos).

**Is this standard?** Yes — enforcing authorization inside the query itself (not as a
separate check after fetching) is the standard fix for IDOR, and "404 over 403" for
ownership failures is a known, deliberate pattern, not just a Noetra choice.

**Docs:** [OWASP: Insecure Direct Object References](https://owasp.org/www-community/attacks/Insecure_Direct_Object_Reference)

---

## The client is never a trust boundary

**The question that came up:** if the React UI never renders a button to fetch *another*
user's repo, why does the API still need to check ownership on every request?

**The answer:** the frontend and backend are two separate programs connected only by
HTTP. The React app is just *one* possible caller of the API — nothing stops a request
from being sent a different way: editing a request in the browser's Network tab and
resending it, hitting the API directly with `curl`/Postman using a valid session cookie,
or scripting a loop over IDs. A session cookie proves *who's asking*, not *what they're
allowed to ask for* — that has to be re-checked by the server on every single request,
regardless of what the UI happens to expose. Relying on "the button doesn't exist" as
protection is called **security through obscurity**, and it's how most real IDOR bugs are
actually found in practice — not through the app's own UI, but by directly editing a
request the UI sent and seeing what comes back.

**Is this standard?** Yes — "never trust the client" is one of the most repeated rules in
web security; any check that matters for security has to live server-side.

**Docs:** [OWASP: Insecure Direct Object References](https://owasp.org/www-community/attacks/Insecure_Direct_Object_Reference) (same root cause as the entry above)

---

## Real-world files break "every tracked path is a normal readable file"

**The pattern:** building the file tree browser, `git ls-files` was assumed to yield a
flat list of ordinary text/binary files. Two different real repos (not toy test repos)
broke that assumption in two different ways:

1. **NUL bytes aren't "binary" by the usual test.** The usual binary-detection trick —
   "try to `.decode('utf-8')`, if it throws, it's binary" — misses NUL bytes (`\x00`),
   because NUL is technically a *valid* UTF-8 character. A file can decode successfully
   and still contain NUL bytes, which Postgres `text` columns reject outright. Fix: check
   for `\x00` in the raw bytes *before* trying to decode, not after.
2. **Symlinks aren't files.** `git ls-files` lists tracked symlinks the same as regular
   files, but a symlink's actual git-tracked content is the *target path string itself*
   (e.g. `"../../../arch/arc/boot/dts"`), not the thing it points to. Reading it the
   normal way either silently reads through the link, or — if the target is a directory
   (real example: the Linux kernel's `scripts/dtc/include-prefixes/arc`) — crashes with
   `IsADirectoryError`. Fix: check `Path.is_symlink()` first and read the link target via
   `Path.readlink()`, which matches what git itself considers that path's content to be.

**Why this matters generally:** code that only gets tested against small/simple repos
will look correct and then fail the first time it meets a large, old, real-world codebase
— these two bugs only ever showed up when testing against `fbsamples/f8app` and the Linux
kernel, never against small test repos like `octocat/Hello-World`.

**Docs:** [git-ls-files](https://git-scm.com/docs/git-ls-files) · [Python `pathlib.Path.readlink`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.readlink)

---

## A caught exception's own string can leak a secret

**The gotcha:** adding a timeout to `git clone` meant catching `subprocess.TimeoutExpired`
— but that exception's default `str()` representation includes the **full command list**
that was run, argv and all. Here, that command list contained the Basic-auth header with
a live GitHub token embedded in it (`Authorization: Basic <base64 token>`). Blindly doing
`repo.error_message = str(exc)` (the generic pattern used for every other unexpected
failure in this task) would have written that token straight into the database, visible
to the user through a plain error message.

**The fix:** catch `TimeoutExpired` in its own `except` block, *before* the generic
`except Exception`, and hand-write a safe message (`f"Clone timed out after {N}s"`)
instead of using the exception's own string form.

**Why this matters generally:** "just log/store `str(exc)`" is a very common pattern for
unexpected errors, and it's usually safe — but any exception whose message can include
data you passed in (command arguments, request bodies, headers) needs to be checked for
what it might be carrying before it's ever surfaced to a user or written to a log/DB.

**Is this standard?** Yes — this is a known category of accidental secret leakage (secrets
ending up in logs/error messages), commonly caught in security reviews of exactly this
kind of "wrap risky operation in try/except, store the error" code.

**Docs:** [Python docs — `subprocess.TimeoutExpired`](https://docs.python.org/3/library/subprocess.html#subprocess.TimeoutExpired)

---

## Redis's two Celery roles: broker vs. result backend

**What it is:** `core/celery_app.py` configures Redis twice — once as `broker`, once as
`backend`. These are two different jobs, easy to conflate because it's the same Redis
instance doing both.

- **Broker** = the message queue. When `api` calls `celery_app.send_task(...)`, that
  pushes a message onto a Redis list; workers block waiting to pop messages off it. This
  is the part actually load-bearing in Noetra — it's how `api` hands work to `worker`.
- **Result backend** = where Celery *would* store a task's return value/status, keyed by
  task ID, if something called `AsyncResult(task_id).get()` to check on it later.

**Why this matters here:** Noetra doesn't use the result-backend half at all — nothing
calls `AsyncResult`. Task/indexing status is tracked a different way: `Repository.status`
in Postgres, updated directly by the worker as it progresses (`queued → cloning → ...`).
That's deliberate, not an oversight — the indexing pipeline is multiple separate Celery
tasks, not one task with one "final result," and the status needs to be queryable
(joined to `owner_id` for ownership checks) in a way a Redis key-by-task-id can't do.

**Is this standard?** Yes — using Celery purely as a task queue (broker) while tracking
your own domain-level status in your real database is a common pattern once a job's
progress needs to be more than "did it return a value."

**Docs:** [Celery: Result Backends](https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-result-backend-settings)

---

## Distributed locks with Redis (`SET key val NX EX ttl`)

**What it is:** a way to make sure only one process, across multiple separate
containers/servers, can do a particular thing at a time — the multi-process equivalent
of a `threading.Lock()`, except a normal Python lock only works within one process's
memory, and `api`/`worker` here are separate processes entirely.

**The building block:** Redis's `SET` command with two flags combined:
- `NX` ("Not eXists") — only set the key if it doesn't already exist. This is what makes
  it a lock: if two requests race to `SET` the same key at the same instant, Redis
  guarantees only one of them gets to actually create it (atomic check-and-set, done as
  one command — no separate "check, then act" steps that could race against each other).
- `EX <seconds>` — auto-delete the key after N seconds, no matter what. Called a
  **lease** rather than a plain lock, because it self-expires. This exists purely as a
  crash safety net: if the process holding the lock dies before it can release the lock
  itself, the lock doesn't get stuck forever — Redis cleans it up on its own.

**Why we need it here:** `api/repos.py`'s `retry_repository` had a real race — two rapid
clicks on "Retry" could both see `status == FAILED`, both re-enqueue
`worker.tasks.clone_repository` for the same repo, and both start deleting/re-cloning
the same on-disk directory concurrently. `core/redis_client.py`'s
`acquire_index_lock`/`release_index_lock` close that: the API tries to acquire a
per-repo lock (`lock:index:{repository_id}`) before enqueueing, and the worker releases
it in a `finally` block once the job ends (success or failure) — so a second concurrent
attempt gets `False` back and returns a 409 instead of racing.

**Why Redis specifically (not Postgres, not a Python variable):** needs to be (1) shared
across separate `api`/`worker` processes — a Python-level lock wouldn't be visible across
containers, (2) atomic in one round trip — the `NX`+`EX` combo does "check, set, and
expire" as a single indivisible operation, and (3) self-cleaning — Redis's `EX` gives
automatic expiry for free; doing the equivalent in Postgres would mean writing and
running your own cleanup job for stale lock rows.

**A known sharp edge (not yet built — no need to yet):** if a task ever ran *longer*
than the lock's TTL, the lock could auto-expire while the task is still legitimately
running, a second process could then acquire a new lock, and the first task's eventual
`release` would delete that *second* process's lock instead of its own — called **lock
stealing**. The standard fix is a **fencing token**: store a unique value per acquire
(not a constant like `"1"`) and only delete on release if the stored value still matches
your own token (needs a small Lua script to stay atomic). Not implemented here because
it's only reachable once a task can outlive the TTL, which can't happen yet — clone jobs
are hard-capped at 300s (`CLONE_TIMEOUT_SECONDS`) against a 600s lock TTL.

**Is this standard?** Yes — `SET NX EX` is the standard simple single-node Redis lock
pattern. The full "Redlock" algorithm (locking across *multiple independent* Redis
nodes) is a different, heavier tool for a different problem (Redis node failover), not
needed here with a single Redis instance.

**Docs:** [Redis `SET` command](https://redis.io/docs/latest/commands/set/) · [Redis distributed locks pattern](https://redis.io/docs/latest/develop/use/patterns/distributed-locks/)

---

## RAG vs CAG (cache-augmented generation)

**What they are:** two ways to get a codebase in front of an LLM.
- **RAG** (retrieval-augmented generation): search the codebase, pull back the ~10 most
  relevant snippets, put only those in the prompt.
- **CAG** (cache-augmented generation): skip searching entirely — put the *whole* codebase
  in the prompt and rely on **prompt caching** so you only pay full price for it once.

**Why prompt caching makes CAG thinkable at all:** providers will cache a long prompt
*prefix*. Send the same first 500k tokens again within the cache window and they're billed
at a steep discount instead of full price. So "just include everything" stops being
obviously insane — with 1M-token context windows it's a real architecture, not a toy.

**Why Noetra uses RAG anyway** — three reasons, in increasing order of how much they hurt:
1. **Size.** ~1M tokens is roughly 3.5MB, roughly 100k LOC. Plenty of real repos are
   10–30x that.
2. **Cost per question.** A cached read of a full repo is roughly 10x what a focused
   ~15k-token RAG context costs, on every single question.
3. **Cache TTL — the actual killer.** Caches expire in minutes to an hour. That's fine for
   one person hammering one repo in one sitting. Noetra is many users x many repos, each
   queried occasionally, so nearly every question would pay the expensive *cache write*
   again. CAG's economics assume warmth that a multi-tenant app cannot maintain.

Plus citations get worse: with CAG the model reports line numbers from memory of a huge
blob and drifts; with RAG the retriever already knows the exact line range.

**How it's used in Noetra:** RAG (see `docs/RETRIEVAL.md`), but two ideas are borrowed
from CAG — (a) keep the prompt prefix byte-stable (system prompt, then tool definitions,
then repo map, and never a timestamp or the user's question early in it) so automatic
prefix caching hits on every follow-up question; (b) a "small repo fits entirely in the
prompt" fast path is noted as a clean V2 seam.

**Is this standard?** RAG is the default for codebase QA. CAG is newer and genuinely used
— for single-user tools on bounded corpora. The trade-off is well known: CAG buys
simplicity and zero index-build time, and pays for it in per-query cost and corpus size.

**Update (provider swap → Gemini):** the borrowed idea still holds, but the mechanism has a
caveat worth knowing. Gemini 2.5 Flash does **implicit caching** — automatic, no API change,
you just get a discount when a request shares a prefix with a recent one. Unlike OpenAI's,
it only kicks in **above a minimum prompt length** (order of ~1k tokens), so a short prefix
gets cached-nothing rather than a small win. That's an argument *for* a substantial repo map
in the prefix, not against one. There is also **explicit caching** (you create a cache
object and reference it) if implicit ever proves too unreliable to depend on.

**Docs:** [Gemini context caching](https://ai.google.dev/gemini-api/docs/caching) ·
[OpenAI prompt caching](https://platform.openai.com/docs/guides/prompt-caching) (the
original reference for this entry)

---

## Why lexical search still beats embeddings on code

**The intuition to unlearn:** "semantic search understands meaning, so it must be better
than keyword matching." True for prose. Much weaker for code.

**Why code is different:** code is not natural language — it is mostly *identifiers*, and
the thing you are looking for is usually spelled out literally somewhere in the file. Ask
"where is `createToken` defined?" and a plain text index nails it instantly. An embedding
model has to represent `createToken` as a fuzzy point in vector space and hope the right
chunk lands nearby. Exact matching wins whenever an exact match exists.

Embeddings earn their keep on exactly one class of question: where the user's words appear
*nowhere* in the codebase — "how does authentication work?" against a file that only ever
says `Session`, `verify`, `cookie`. That is a real and important class. It is also a
minority of questions.

**How it's used in Noetra:** this is why the build order puts lexical retrieval in
milestone 4 and embeddings in milestone 7 — and why they get fused rather than picking
one. A structural retriever (trigram lookup over `code_entity`) also shipped briefly in
M5, then got cut: an ablation against the eval set showed it moved recall@5 by only +0.04,
concentrated in one edge case (identifiers with characters the text-search tokenizer
mangles) — everywhere else, lexical alone already found the same chunk, because chunking
is AST-aware so a function's own definition line is usually its highest-signal chunk
anyway. This is also why a query that looks like a bare identifier gets routed straight to
lexical's exact-match path with no embedding API call at all (~10 ms instead of ~100 ms,
for a *better* answer).

**Is this standard?** Increasingly yes — "agentic search" (give the model `grep` plus
`read_file` plus symbol lookup and let it explore) has become a mainstream alternative to
vector-first RAG for code specifically. Claude Code itself ships with no vector index.

---

## Postgres full-text search: `tsvector`, GIN, and generated columns

**What it is:** Postgres can do real text search natively — no Elasticsearch needed.
- `to_tsvector('english', text)` turns a document into a **`tsvector`**: a normalized bag
  of searchable words (lowercased, stemmed so "running" becomes "run", stopwords dropped).
- A **GIN index** (Generalized Inverted Index) over that column makes matching fast. It is
  an *inverted* index: instead of row to words, it stores word to list of rows containing
  it, which is exactly the lookup a search query needs.
- **`pg_trgm`** is a separate extension for *fuzzy/substring* matching (breaks text into
  3-character chunks). Useful for partial identifier matches where stemming does not help.

**Generated column — the part that matters here:** rather than computing the `tsvector` in
application code and remembering to update it, declare it as a `GENERATED ALWAYS AS
(to_tsvector('english', coalesce(content, ''))) STORED` column. Postgres recomputes it
automatically on every insert and update. There is no indexing step to run, no background
job, and no way for the index to drift out of sync with the content.

**Why we need it here:** it is what makes the revised build order possible. `File.content`
is already persisted at clone time, so adding this column is *one migration and zero
pipeline cost* — full-text search over the entire repo works the moment cloning finishes,
long before parsing or embedding exist. That is enough retrieval to build the agent, the
citation UI, and the eval harness against.

**How it's used in Noetra:** `file.content_tsv` (see `docs/DATA_MODEL.md`), queried by the
lexical retriever in `core/retrieval`. `pg_trgm` on `code_entity.name` for fuzzy symbol
lookup.

**Is this standard?** Yes, for anything short of dedicated-search-engine scale. Reaching
for Elasticsearch before outgrowing Postgres FTS is a classic premature-infrastructure
mistake — it is a whole second datastore to run, sync, and keep consistent.

**Docs:** [Postgres full-text search](https://www.postgresql.org/docs/current/textsearch.html) · [generated columns](https://www.postgresql.org/docs/current/ddl-generated-columns.html) · [pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)

---

## Contextual retrieval (prefixing chunks before embedding)

**The problem:** you chunk code by function so chunks do not split mid-function. But a
function body on its own is often *ambiguous*. `def refresh(self, token: str)` — is that a
cache, a session, an OAuth token, a UI component? The embedding model cannot tell, so the
vector lands somewhere generic and the chunk never surfaces for "how does auth work?"

**The fix:** before embedding, prepend the chunk's context — file path, enclosing class,
signature:

```
src/auth/tokens.py > class TokenService > def refresh(self, token: str) -> Token
<the actual chunk source>
```

Now the vector lands near "authentication" in embedding space, because the text says so.
Embed the prefixed version; return the raw chunk to the model.

**Why it's a good deal:** it is an f-string. No extra API calls, no schema change beyond
storing what you embedded, no latency. It is the cheapest meaningful recall improvement
available at that step.

**How it's used in Noetra:** `chunk.embed_text` stores the prefixed version (so a re-embed
is reproducible and you can see what the model actually saw); `chunk.content` stores the
raw source that goes back to the LLM. See `docs/RETRIEVAL.md`, chunking rule.

**Is this standard?** Yes — Anthropic named and popularized the technique as "contextual
retrieval." The fuller version uses an LLM to write a sentence of context per chunk; the
cheap version (deterministic path/class/signature prefix, what Noetra does) captures much
of the benefit for none of the cost.

**Docs:** [Anthropic — Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)

---

## Reranking, and why RRF is not enough on its own

**Reciprocal Rank Fusion (RRF)** merges several ranked lists into one. Its trick is that it
only looks at *positions*, never scores — so you can fuse a BM25 text score, a symbol-table
hit, and a cosine similarity without any calibration between them. That is exactly why it
is used: three retrievers, three incomparable score scales, one merged list, no tuning.

**Its limitation follows from the same trick:** RRF has no idea what the query *means*. A
result that landed at position 3 in the lexical list gets credit for being at position 3,
whether or not it actually answers the question.

**A reranker** is a model that does look at meaning: give it the query and a candidate, it
scores how well that candidate answers *that specific query*. It is more accurate than
embedding similarity because it reads the query and the document together, rather than
comparing two vectors computed independently. It is also far too slow to run over the whole
corpus — which is the point of the pipeline shape:

```
3 retrievers -> ~30 candidates (RRF, fast, meaning-blind)
             -> ~8 results     (rerank, slow, meaning-aware)
             -> the model
```

**Fuse wide, cut narrow.** Retrieval's job is to not *miss* the right file (recall);
reranking's job is to make sure it is in the top few (precision). Answer quality depends
far more on what is in the top 8 than what is in the top 30.

**Why this also explains the eval metric:** Noetra scores retrieval with **recall@k**, not
precision — because the reranker and then the LLM both get a chance to discard bad hits,
but neither can recover a correct file that retrieval never surfaced at all.

**Is this standard?** Yes — retrieve-then-rerank is the standard two-stage information
retrieval architecture, long predating LLMs.

**Docs:** [RRF paper (Cormack et al.)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)

---

## HNSW vs IVFFlat (pgvector index types)

**The problem both solve:** finding the nearest vectors to a query vector by brute force
means comparing against every row. Fine at 1,000 chunks, not at 500,000. Both index types
are **ANN** — Approximate Nearest Neighbour — trading a little accuracy for a lot of speed.

**IVFFlat** clusters the vectors into `lists` buckets up front, then at query time only
searches the few buckets nearest the query. Downsides: it must be **trained** on existing
data (so you have to load rows *before* building the index), and you have to pick a good
`lists` count for your row count — get it wrong and recall or speed suffers.

**HNSW** (Hierarchical Navigable Small World) builds a layered graph of vectors and walks
it from coarse to fine, like zooming in on a map. No training step, no data required
before building, better recall at the same speed. It costs more memory and is slower to
build at very large row counts.

**Why Noetra uses HNSW:** no training step and no tuning pass to get wrong, and V1 is
nowhere near the scale where IVFFlat's faster build time matters. Fewer knobs, better
recall.

**Is this standard?** Yes — HNSW is the default recommendation for pgvector unless you are
at a scale where build time or memory becomes the binding constraint.

**Docs:** [pgvector indexing](https://github.com/pgvector/pgvector#indexing)

---

## Evaluating retrieval: `recall@k`

**The problem it solves:** "the answers feel good" is not a measurement. Every retrieval
change — contextual prefixes, reranking, adding embeddings at all — is a guess unless
something says whether it helped.

**What an eval set is:** a fixed list of questions with *known correct answer locations*.
For Noetra, ~40 questions across 2–3 public repos **pinned to a commit SHA** (so the
answers do not move under you):

```yaml
- q: "Where is the session cookie signed?"
  repo: noetra-fixtures/flask-sample@a1b2c3d
  answers:
    - { path: "app/session.py", lines: [40, 68] }
```

**`recall@k`** is: of all the questions, what fraction had a correct location somewhere in
the top `k` retrieved results. `recall@5` and `recall@20` are the two worth tracking.

**Why recall and not precision:** precision asks "how much of what I returned was good?"
Recall asks "did I find the right thing at all?" Downstream, the reranker and then the LLM
both get to throw away bad results — but neither can recover a file retrieval never
surfaced. A miss at the retrieval stage is unrecoverable; noise is not.

**How it's used in Noetra:** a pytest that runs each question through `core/retrieval` and
prints the numbers, cheap enough to run on every retrieval change. It is the reason
embeddings are milestone 7 rather than 5 — the plan is to establish a lexical-only
baseline, find which questions fail, then build semantic retrieval against that evidence
with the tuning knobs set by measurement instead of intuition.

**Is this standard?** Yes — recall@k, precision@k, MRR and nDCG are the standard IR
metrics, and building a small labelled eval set before tuning a retrieval system is
ordinary practice. It is also the most interview-legible artifact in the project:
"lexical-only recall@5 was 0.61; AST-chunked embeddings took it to 0.84" beats
"I integrated pgvector."

---

## Tree-sitter: a parser engine + swappable grammars

**What it is:** a parsing library (built by GitHub) that turns source code text into a
**concrete syntax tree (CST)** — a tree where a function is a node containing a name
node, a parameter-list node, a body node, etc., instead of just being characters in a
string. The parsing *engine* is one package (`tree-sitter`); each language's grammar
(the actual rules for what "a function looks like" in that language) ships as its own
separate package (`tree-sitter-python`, `tree-sitter-javascript`, ...).

**Why it beats a language-specific tool (like Python's `ast`):**
1. **One API, many languages.** `ast.parse()` only understands Python. `indexer` needs
   Python, JS, *and* TS behind one code path — Tree-sitter's `Parser`/`Node`/`Tree`
   classes work identically no matter which grammar is loaded.
2. **Error-tolerant.** It produces the best tree it can even around a syntax error
   elsewhere in the file (a real concern across an entire real-world repo).

**The trade-off:** Tree-sitter's tree is *syntactic* only — it knows "this is a call
expression," not "this call resolves to that specific imported function." That's exactly
why import *resolution* (mapping `.utils` to an actual file) had to be a separate step
(`indexer/graph.py`) built on top of parsing's output, not part of parsing itself.

**A gotcha worth remembering:** TypeScript and TSX (TS + embedded JSX) can't share one
grammar the way JS and JSX can — TS generics (`<T>`) and JSX tags are ambiguous in a
couple of spots. `tree-sitter-typescript` ships *two* separate compiled languages in one
package (`language_typescript()` and `language_tsx()`) specifically because of this.

**How it's used in Noetra:** `indexer/parser.py` — one `Parser` instance per file
extension, all built from this one library. Confirmed the exact node/field names
(`variable_declarator.value`, `import_from_statement.module_name`, etc.) by literally
parsing sample snippets and printing the tree, rather than guessing from documentation.

**Is this standard?** Yes — Tree-sitter is what GitHub's own code navigation/highlighting
uses, and it's become the default choice for any tool that needs multi-language,
error-tolerant parsing (linters, editors, static analysis).

**Docs:** [Tree-sitter — using parsers](https://tree-sitter.github.io/tree-sitter/using-parsers)

---

## Postgres transactional DDL (schema changes roll back too)

**What it is:** in Postgres, `CREATE TABLE`/`ALTER TABLE`/etc. are transactional just
like `INSERT`/`UPDATE` — if a migration runs several DDL statements and one fails partway
through, *everything* in that transaction (including the tables/columns that were
already successfully created earlier in the same migration) gets rolled back together.

**Why this matters:** hit this directly — a migration that created `code_entities`
successfully, then failed on a later `ALTER TABLE files ADD COLUMN language ...`. Despite
the partial success, `alembic current` afterward still showed the *previous* revision,
and `code_entities` didn't exist in the database at all. Nothing needed manual cleanup;
the whole migration had already been undone automatically.

**Why this is worth knowing generally:** not every database gives you this for free —
MySQL, for example, does **not** roll back DDL on failure, so a multi-statement MySQL
migration that fails halfway through can leave a genuinely inconsistent schema that
needs manual repair. Alembic prints "Will assume transactional DDL" precisely because
this behavior is database-specific, not a universal guarantee.

**Is this standard?** Yes, for Postgres specifically — one of the reasons Postgres is
often preferred for schema-migration-heavy applications.

**Docs:** [Postgres — DDL and transactions](https://www.postgresql.org/docs/current/ddl.html) (see the general note on transactional DDL under "Overview")

---

## Postgres enums + Alembic: `CREATE TABLE` auto-creates the type, `ALTER TABLE` doesn't

**The gotcha:** adding a new table with an enum column (`code_entities.kind`) worked with
zero extra effort — SQLAlchemy/Alembic automatically ran `CREATE TYPE entity_kind AS
ENUM(...)` right before the `CREATE TABLE`. Adding an enum column to an *already-existing*
table (`files.language`) via plain `op.add_column(...)`, using the exact same `sa.Enum(...)`
syntax, failed outright: `psycopg.errors.UndefinedObject: type "file_language" does not
exist`.

**Why they behave differently:** `CREATE TABLE` is understood by SQLAlchemy as "building
this whole thing from scratch," so it walks every column's type and creates anything that
needs creating first. A bare `ALTER TABLE ... ADD COLUMN` is a much narrower operation —
Alembic doesn't infer "and also create this brand-new type" from it; it assumes the type
already exists.

**The fix:** create the enum type explicitly first, then tell the column not to try
creating it again:
```python
file_language_enum = postgresql.ENUM("PYTHON", "JAVASCRIPT", "TYPESCRIPT", name="file_language")
file_language_enum.create(op.get_bind(), checkfirst=True)
op.add_column("files", sa.Column("language", sa.Enum(..., name="file_language", create_type=False)))
```
And the reverse on `downgrade()`: dropping the column doesn't drop the type it referenced
— that needs its own explicit `postgresql.ENUM(name=...).drop(...)`.

**Is this standard?** Yes — a well-known Alembic/Postgres-enum rough edge, not a
Noetra-specific bug. Worth remembering any time an enum column gets added to a table that
already exists, rather than created alongside it.

**Docs:** [Alembic — PostgreSQL ENUM cookbook recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html) (search "postgresql-enum")

---

## Alembic autogenerate can't see hand-written raw-SQL DDL

**The gotcha:** a migration added two GIN indexes via `op.execute("CREATE INDEX ...")`
instead of a SQLAlchemy `Index(...)` object, because they needed a non-default operator
class (`gin_trgm_ops`) that plain `Index()` can't express. The *next* migration's
`--autogenerate` run flagged both as "removed" and generated `drop_index` calls for
them — because autogenerate diffs the live database against `Base.metadata`, and
anything created outside that metadata (raw SQL) is invisible to it. From
autogenerate's point of view, a real index it doesn't know about looks identical to one
that used to be declared and got deleted.

**Why this matters:** blindly running `alembic upgrade head` on an autogenerated
migration without reading it first would have silently dropped two working, real
indexes that lexical/fuzzy search depends on.

**The practical rule this confirms:** always read a generated migration's `upgrade()`
and `downgrade()` before applying it — autogenerate is a diffing *tool*, not a proof
that the result is correct. This is exactly why Alembic prints "please adjust!" in its
generated comments.

**Is this standard?** Yes — this is a known, documented limitation of any ORM-metadata-
diffing autogenerate tool, not specific to this index or to Noetra.

**Docs:** [Alembic — autogenerate limitations](https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect)

---

## Precomputed index vs. repeated linear scan (a classic time/space trade-off)

**The bug:** resolving one Python absolute import (`import pkg.mod`) to an actual file
scanned *every* known file path in the repo, checking if any of them ended with the
right suffix. Fine on a 12-file repo. On `tensorflow/tensorflow` (36k files, thousands
of absolute imports), that's thousands of full 36k-item scans — tens to hundreds of
millions of string comparisons, and it showed up as an ~8-minute stall.

**The fix:** build one lookup structure — a dict mapping every possible path *suffix*
(`"mod.py"`, `"pkg/mod.py"`, `"src/pkg/mod.py"`, ...) to the real path(s) that end that
way — **once per repo**, before resolving any imports. After that, each import is a
single dict lookup (O(1) on average) instead of a fresh scan.

**The general pattern:** this is "do a little more work up front (build an index) to
make every later lookup cheap," the same idea behind a database index, a hash map, or a
compiler's symbol table. It costs a bit of memory and a one-time pass over the data; it
pays for itself the moment you look something up more than once. The tell that you need
one: doing the *same kind* of scan repeatedly over data that isn't changing between
scans.

**How it's used in Noetra:** `indexer/graph.py`'s `build_suffix_index`, called once per
repo inside `resolve_dependencies` before the per-import resolution loop.

**Is this standard?** Yes — recognizing "O(n) work happening inside a loop that runs m
times, when the data being scanned doesn't change" as an O(n×m) bug, and fixing it with a
precomputed index, is one of the most common real-world performance fixes there is.

---

## Deferred cleanup: the fast path deletes the record, a background task cleans up the side effect

**The gap:** deleting a repo removed its database rows (correctly, via Postgres's own
cascading foreign keys) but left its cloned files sitting on disk forever — nothing ever
told the worker's storage volume that repo was gone.

**Why the fix isn't "just call `shutil.rmtree` in the delete endpoint":** the clone
directory can be many gigabytes with tens of thousands of files; deleting it can take
real time. `CLAUDE.md`'s module boundary already draws this line elsewhere in the
project — `api` is the fast HTTP layer and must never do slow, repo-touching work
inline; that always belongs in a `worker` background task. Deleting a repo is no
different from cloning one in that respect.

**The pattern:** the API deletes the *authoritative* record (the DB row) and returns
immediately — from the user's perspective, the repo is already gone. It then enqueues a
Celery task to clean up the *derived* side effect (the on-disk files) whenever the
worker gets to it. The two don't need to happen atomically together: nothing else in the
system reads that directory once the DB row is gone, so a short delay before the actual
disk space is reclaimed is harmless.

**Why this matters generally:** this is a small instance of a common distributed-systems
shape — separating "the operation the user is waiting on" (fast, synchronous, strongly
consistent) from "necessary cleanup of a side effect" (slow, asynchronous, only
eventually consistent). Trying to make both parts happen together, synchronously, is
what leads to slow endpoints or half-finished operations when the slow part fails.

**How it's used in Noetra:** `api/repos.py`'s `delete_repository` deletes the `Repository`
row, then `celery_app.send_task("worker.tasks.delete_repository_clone", ...)`; the new
`worker.tasks.delete_repository_clone` task does the actual `shutil.rmtree`.

**Is this standard?** Yes — "delete the record now, clean up storage later via a
background job" is the standard shape for any resource with an expensive-to-remove
side effect (large file uploads, temp directories, cache entries tied to a deleted
record, etc.).

---

## `recall@k` and the "eval harness"

**What it is:** `recall@k` measures a search system: *of all the correct answers that
exist, how many did the search surface in its top `k` results?* `recall@k = (relevant
items found in top k) / (total relevant items)`. Example: "where is `clone_repository`
defined?" has one correct location; if the search returns it anywhere in the top 5,
`recall@5 = 1/1 = 100%` for that question; average over ~40 questions to score the whole
retriever. An **eval harness** is the test infrastructure that runs a fixed set of inputs
through the system and auto-scores the output against known-correct answers — same idea as
a unit-test suite, but scored with a metric instead of pass/fail.

**Why we need it here:** retrieval quality is the whole product, and "the answers feel
good" isn't a measurement. Without a number, every later change (add a retriever, change
chunking, tune `k`) is guesswork and no regression can be attributed to a specific cause.

**How it's used in Noetra:** `backend/eval/` — `questions.yaml` (~40 questions, each with
known answer `path`+`lines`, tagged `symbol`/`keyword`/`conceptual`), `seed.py` (indexes 3
SHA-pinned repos), and `run.py` (runs each question through `core.retrieval.search()` and
prints `recall@5`/`recall@20`, broken down by kind + repo). The `conceptual` row is the
go/no-go signal for whether M7 embeddings are worth building.

**Family:** `recall@k` is order-*unaware* (in-top-k or not). Order-aware cousins: **MRR**
(rewards the first correct hit being near rank 1), **MAP@k**, **NDCG@k** (graded
relevance). We use `recall@k` because our ground truth is binary (a location is correct or
not) — MRR is the natural add-on later if recall alone stops being diagnostic.

**Is this standard?** Yes — the standard offline-evaluation setup for any
retrieval/RAG/search system; NDCG is the most common in IR research.

**Docs:** [Pinecone — offline retrieval evaluation](https://www.pinecone.io/learn/offline-evaluation/)

---

## Postgres full-text search (`tsvector` / `tsquery` / `ts_rank`)

**What it is:** Postgres's built-in keyword search. It preprocesses text into a `tsvector`
— a list of normalized, *stemmed* words with positions ("running"→"run") — and matches it
against a `tsquery` with the `@@` operator. `ts_rank` scores how well a document matches,
for ordering. Trigram search (`pg_trgm`) is a *separate*, fuzzier tool: it breaks strings
into 3-char chunks and matches by overlap, so `createTok` finds `createToken` — good for
identifiers/typos, where stemming-based full-text is wrong.

**Why we need it here:** it's the "lexical" leg of retrieval — cheap, exact-ish keyword
matching over file content and symbol names, already indexed at the DB level (a GIN index
on `content_tsv`, a trigram GIN index on `code_entities.name`).

**How it's used in Noetra:** `core/retrieval/lexical.py` runs
`content_tsv @@ websearch_to_tsquery('english', :q)` ordered by `ts_rank`. A trigram-based
`structural.py` retriever also matched symbol names with the `%` operator + `similarity()`
for a while (M5), but was removed after measurement showed it moving recall@5 by only
+0.04 — see `docs/RETRIEVAL.md`'s decision record. **Key choice — `websearch_to_tsquery`**
(not `to_tsquery` or
`plainto_tsquery`): it accepts raw Google-style user input (`auth OR "session cookie"`)
and *never raises* on junk, whereas `to_tsquery` 500s on a stray space. That safety is why
it's the right pick for a user-facing search box.

**Is this standard?** Yes — `tsvector`+GIN is the standard way to do full-text search in
Postgres without a separate engine (Elasticsearch etc.); `pg_trgm` is the standard
fuzzy/substring companion.

**Docs:** [Postgres full-text search](https://www.postgresql.org/docs/current/textsearch.html) ·
[pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)

---

## Reciprocal Rank Fusion (RRF)

**What it is:** a way to merge several ranked lists into one using only each item's *rank
position* — not the retrievers' raw scores. Formula: an item's fused score is `sum over
lists of 1/(k + rank)`, with `k` a dampening constant (60 is the standard from the
original paper). An item ranked #1 in a list contributes `1/61`; #2 contributes `1/62`;
items that rank well in *several* lists rise to the top.

**Why we need it here:** retrievers' scores aren't comparable — `ts_rank` (a full-text
relevance float) and a semantic retriever's cosine similarity (0–1) would live on totally
different scales. Averaging them would be meaningless. RRF sidesteps the problem by
throwing away the scores and using only rank order.

**How it's used in Noetra:** `core/retrieval/fusion.py::reciprocal_rank_fusion` merges
ranked lists, keys each hit by its code location, sums `1/(k+rank)`, unions the source
retrievers on duplicates, and returns the top hits. It briefly fused lexical + a
structural (trigram) retriever in M5, then went dormant — the structural leg was cut after
an ablation showed it moving recall@5 by only +0.04 (see `docs/RETRIEVAL.md`), and one
list alone needs no fusion. It's wired back in — just one more list in the input, no other
code changes — once the M7 semantic leg ships. (Current limitation: it dedupes by *exact*
line range, so the same location surfaced with slightly different ranges by two
retrievers isn't merged yet — deferred until the eval shows it matters.)

**Is this standard?** Yes — RRF is the go-to fusion method for hybrid search (keyword +
vector); popular precisely because it's robust and needs zero score calibration.

**Docs:** [Cormack et al., 2009 — the RRF paper (PDF)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)

---

## Repo map (PageRank over the import graph)

**What it is:** a compressed, names-only "table of contents" of a codebase — every file
with just its top-level symbols (no bodies) — handed to an AI agent *before* it starts
searching, so it orients from a floor plan instead of blindly guessing search terms.
Because a big repo has too many symbols to list them all, you rank them with **PageRank**
over the import graph (the same "important if many important things link to it" algorithm
Google used for web pages) — a file many files import is probably central, so its symbols
make the map; leaf files get trimmed.

**Why we need it here:** agentic search (how the chat agent works — search/read in a loop,
like Claude Code) has one weak moment: the *first* tool call, where it must guess a search
term with no sense of the codebase's shape. That's worst on vague questions ("how is auth
implemented?"). The repo map replaces that blind first guess with an informed one — built
entirely from data we already have, with zero AI calls.

**How it's used in Noetra:** scoped into **Milestone 8** (not built yet — renumbered when
the build order moved to agentic RAG). It'll be built from `code_entity` (symbol names) +
`dependency_edge` (import graph → PageRank centrality) and placed in the agent's byte-stable
prompt prefix, so Gemini's implicit prefix caching keeps it cheap per follow-up (see the CAG
entry above for the minimum-prompt-length caveat). Caveat: raw centrality over-ranks generic
utilities (a `utils.py` everyone imports) — PageRank dampens but doesn't fully fix this;
fine, because the map only needs to *orient* the agent, which then verifies by reading files.

**Is this standard?** The pattern comes from **aider** (an open-source coding agent), which
runs PageRank over the repo's dependency graph to build its "repo map". Using a lightweight
structural map to steer an agent is an increasingly common technique.

**Docs:** [aider — repository map](https://aider.chat/docs/repomap.html)

---

## Commit SHA & "pinning" to one

**What it is:** a **SHA** is the ID git computes for every commit from its exact contents
(file tree + message + parent + author). Change one byte → completely different SHA. The
key property: a SHA points at one **immutable, frozen snapshot** of the code, forever —
called *content-addressing* (the address is a fingerprint of the content). "Pinning" means
referring to code by its SHA instead of by a moving name.

**Why we need it here:** the eval's answer keys ("question X → `fusion.py` lines 23–48") are
only correct against one exact version of the code. A **branch** name (`main`) moves every
time upstream commits — line 40 becomes line 55 tomorrow — and even **tags** can be
re-pointed. A SHA can't move, so the keys stay valid forever. It matters double for noetra,
since we actively edit it: pinning to a SHA and indexing that snapshot keeps its keys stable
no matter how much `develop` changes afterward.

**How it's used in Noetra:** `eval/repos.py` pins each eval repo to a 40-char SHA;
`eval/seed.py` does `git fetch --depth 1 origin <sha>` + `git checkout <sha>` to get that
exact tree.

**Is this standard?** Yes, everywhere — pinning dependency versions in a lockfile, pinning a
Docker image by digest (`@sha256:…`) instead of `:latest`. The pattern is always: *replace a
name that can move with a fingerprint that can't, for reproducibility.*

**Docs:** [Git — commit objects & SHAs](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

---

## Query relaxation (`websearch_to_tsquery` joins bare terms with AND)

**What it is:** when you hand `websearch_to_tsquery` a plain sentence, it ANDs every
surviving word together. `"where are user credentials protected before being written to the
database?"` compiles to `'user' & 'credenti' & 'protect' & 'written' & 'databas'` — a
document must contain **all five** to match at all. Stopwords ("where", "are", "the") get
dropped, but the rest are mandatory. **Query relaxation** means retrying with the terms
OR'd together when the strict form returns too little.

**Why we need it here:** this single behaviour was holding conceptual `recall` at exactly
**0.00**. Not "ranked badly" — the retriever was returning an *empty list*, because almost
no file contains every word of a natural-language question. Any amount of ranking work
downstream is worthless if the candidate set is empty.

**How it's used in Noetra:** `core/retrieval/lexical.py` runs the strict query first, and
only if it returns fewer than `limit` hits does it run a second pass with the terms joined
by ` OR ` and backfill the empty slots. Strict hits keep their positions, so the change
**cannot** regress precision — and the eval proved it, since the symbol and keyword buckets
came back byte-identical while conceptual moved. The OR query is built in *websearch
syntax* (not raw `|` operators) so Postgres still does the stemming and still never raises
on junk.

**Is this standard?** Yes. Elasticsearch exposes it directly as `minimum_should_match`
("match at least N of these terms"). Postgres has no equivalent, so the two-pass fallback
is how you get the same behaviour.

**Docs:** [Postgres — controlling text search](https://www.postgresql.org/docs/current/textsearch-controls.html) ·
[Elasticsearch — `minimum_should_match`](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-minimum-should-match)

---

## Vocabulary drift: match with the same stemmer the index used

**What it is:** a full-text index doesn't store your words, it stores **lexemes** — stemmed,
lowercased, stopword-filtered tokens. "credentials" is stored as `credenti`. If some other
part of your code later tries to answer "where did this match?" using the *raw* words, it's
searching a different vocabulary than the one that matched. That mismatch is vocabulary
drift.

**Why we need it here:** `_best_line` picked which line to cite by checking whether a query
word appeared *literally* in the line. The index had matched on stems. So a file could match
on `credenti` while the literal string "credentials" appeared nowhere — every line scored
zero, and the function returned **line 1**. Retrieval found the right file and then threw the
citation away. Six of nine remaining misses were this one bug.

**How it's used in Noetra:** the fix is to *ask the engine for its own vocabulary* rather
than re-derive it. `core/retrieval/lexical.py::_query_lexemes` runs
`tsvector_to_array(to_tsvector('english', :query))`, which returns exactly the lexemes
Postgres would use, then matches source words by **stem prefix** (`credenti` is a prefix of
"credentials"). The tempting alternative — writing a stemmer in Python — would have
reintroduced the exact drift being fixed, and would silently diverge whenever Postgres
changed.

**Is this standard?** The principle is general and worth naming: **never reimplement a
component's normalization; ask it what it did.** Same reason you compare passwords with the
library's `verify()` instead of re-hashing yourself, and why `ts_headline` exists rather than
having you locate matches by hand.

**Docs:** [Postgres — text search functions](https://www.postgresql.org/docs/current/functions-textsearch.html)

---

## AST chunking (the retrieval unit decides how good a citation can be)

**What it is:** an **AST (abstract syntax tree)** is the structured tree a parser builds out
of source code. Instead of a flat wall of text, the file becomes "this module contains a
class, that class contains these three methods, each method runs from line X to line Y".
Tree-sitter is what produces it here. **AST chunking** means cutting a file into search units
along those **syntax boundaries** — one chunk per function/method — instead of by a fixed
line or token count. The rule is that a chunk must never split a function in half, because
half a function retrieves as noise.

**Why we need it here:** the unit you index is the unit you can cite. Indexing whole *files*
means a hit is "this file matched" and something has to *guess* which line to point at —
which is exactly the bug above. Indexing *chunks* means the line range comes free and exact,
because the chunk already knows its own boundaries. It also decides what the M6 agent
receives: a file-level hit forces a second `read_file` call that pulls ~1,200 lines into
context to answer a question about 25 of them; a chunk-level hit *is* the answer.

**How it's used in Noetra:** `indexer/chunker.py` (pure, no DB) emits one chunk per **leaf
entity** — an entity containing no other entity, so a class produces chunks for its methods
rather than a second copy of the whole class — plus **gap chunks** covering every line no leaf
covers (imports, module constants, class headers, and whole files with no entities at all,
like markdown or JSON). Gaps have no syntax to damage, so only they get split at a fixed size
(`MAX_GAP_LINES = 80`). Measured effect: keyword `recall@5` went 0.79 to 0.93.

**Is this standard?** Yes, and it's the consensus for code specifically — fixed-size chunking
is the default for prose but actively harmful for source, where a function is the natural
semantic unit. Note chunking is **independent of embeddings**: it's a prerequisite for them,
but it pays off on its own through exact citations and smaller agent payloads.

**Docs:** [Anthropic — contextual retrieval](https://www.anthropic.com/news/contextual-retrieval)

---

## Result diversity ("collapsing") and the SQL window function that does it

**What it is:** capping how many results a single group (here, one file) may contribute, so
one strongly-matching document can't fill the entire result list and hide everything else.
Search engines call this **collapsing**; the general idea is trading a little raw relevance
for coverage.

**Why we need it here:** switching to chunks quietly *regressed* `recall@20` from 0.79 to
0.74. The cause wasn't ranking — it was arithmetic. Twenty slots used to mean twenty distinct
**files**; with chunks it could mean twenty chunks from **ten** files. On one query,
`docs/CONCEPTS.md` alone took **8 of 20 slots**. The file holding the answer never appeared,
and a caller can't recover a file it was never shown.

**How it's used in Noetra:** `core/retrieval/lexical.py` uses a **window function** —
`row_number() OVER (PARTITION BY file_id ORDER BY rank DESC)` — then keeps only rows where
that number is at most `_MAX_CHUNKS_PER_FILE`. A window function computes a value *per row
relative to a group of other rows*, without collapsing them the way `GROUP BY` does; here it
ranks each file's chunks against each other. It has to happen in SQL, in a subquery, because
`LIMIT 20` would otherwise have already discarded every file past the crowding one — capping
in Python afterwards would be too late.

**Is this standard?** Yes — Elasticsearch has a `collapse` parameter for exactly this;
maximal marginal relevance (MMR) is the more general form used in RAG pipelines. Window
functions are core SQL, not a Postgres extension.

**Docs:** [Postgres — window functions](https://www.postgresql.org/docs/current/tutorial-window.html) ·
[Elasticsearch — collapse](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/collapse-search-results)

---

## Query-time boosting (weighting one class of document above another)

**What it is:** multiplying a relevance score up or down based on what *kind* of document it
is, rather than how well it matched. A factor of 0.3 on a class means those documents need
roughly 3x the raw score to outrank a normal one.

**Why we need it here:** an `english` text-search config is built for English prose, so on a
natural-language query it ranks *writing about* code far above the code itself. Diagnosing the
chunking regression showed noetra's entire top-20 was `docs/*.md` — not one source file — and
requests was returning `HISTORY.md` and **`LICENSE`** ahead of the module that implements the
behaviour. For a tool whose product is `file:line` citations into source, that's simply wrong.

**How it's used in Noetra:** `core/retrieval/lexical.py` multiplies `ts_rank` by
`_NON_SOURCE_RANK_FACTOR = 0.3` where `file.language IS NULL` — which is already exactly the
non-py/js/ts set, so no new data was needed. Docs stay reachable, just below code. This was
the single largest fix of the session: conceptual `recall@5` went 0.14 to 0.36 and overall
`@20` went 0.76 to 0.81.

**Is this standard?** Yes — per-field and per-index boosts are a basic feature of every search
engine (Elasticsearch `boost`, Lucene field weights). The general lesson is that *relevance
and importance are different things*, and a scorer only knows the first one.

**Docs:** [Elasticsearch — bool query and boosting](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-bool-query)

---

## When your eval can't referee the change you're making

**What it is:** a benchmark can only judge a change if its answer key is *neutral* about that
change. If every correct answer happens to sit in the category you just promoted, the score
goes up whether or not the change was good — the metric is measuring your assumption back at
you rather than testing it.

**Why we need it here:** all 42 original questions had answers in **source files**. So
demoting non-source files (the boost above) was guaranteed to improve the number, no matter
how far the penalty was cranked. Setting the factor to 0.001 would have "scored better" while
making documentation permanently unreachable. The number was real; its *interpretation* wasn't
safe.

**How it's used in Noetra:** `eval/questions.yaml` gained a fourth question kind, `docs`, with
4 questions whose answers genuinely live in prose (`DEPLOYMENT.md`, `quickstart.rst`,
`advanced.rst`, `metadata.mdx`). They aren't retrieval targets — they're a **guard-rail**: if
the penalty is ever tuned too hard, that row collapses and says so. It reported `@5 0.75` /
`@20 1.00`, confirming 0.3 demotes docs without burying them. Making it a *separate kind*
rather than more `conceptual` questions kept every existing bucket's denominator unchanged, so
all earlier runs stayed comparable.

**Is this standard?** The failure mode has names — *benchmark gaming*, *construct validity*,
and (when the metric becomes the target) **Goodhart's law**. The habit worth keeping: whenever
you add an optimization, ask *"could this metric go up while the product gets worse?"* If yes,
add the case that would catch it **before** trusting the number.

**Docs:** [Goodhart's law](https://en.wikipedia.org/wiki/Goodhart%27s_law)

---

## Who calls a component changes what it gets fed (input distribution)

**What it is:** the same function can be handed completely different-looking inputs depending
on who is calling it, and a benchmark only tests the caller it imitates. The mix of inputs a
component actually sees in production is its **input distribution**. Measure against the wrong
one and you can spend weeks optimizing a case that never occurs — or miss one that does.

**Why we need it here:** `core/retrieval.search()` has two callers with very different habits.
The `/search` endpoint hands it whatever a human typed into a box, so it really does receive
`"Why do header lookups work no matter how you capitalize them?"` word for word. The M6 chat
agent will not: it reads the question, works out that the codebase probably calls this thing a
"case-insensitive dict", and calls `code_search("case insensitive headers")`. Same function,
two different worlds.

The eval harness only imitates the first caller — it feeds every question in verbatim. So the
`conceptual` bucket's low score is an **honest** measure of the search UI and a **pessimistic**
one for the agent, because it charges retrieval for a translation step the agent would have
done first. That distinction matters a lot, because the conceptual bucket is the main evidence
in the "do we need embeddings (M7)?" decision — and half of what it's currently measuring is a
step that won't exist in the agent path.

**How it's used in Noetra:** noted so it doesn't get forgotten: when M6 lands, `eval/run.py`
should score **agent-mediated** retrieval next to raw `search()` — same 42 questions, two
columns. That's the only way the repo map (which never touches `search()` and so cannot move
today's numbers at all) can earn its place the way every retriever has had to.

**Is this standard?** Yes, and it's one of the most common ways benchmarks mislead. It's the
same reason a model evaluated on clean text degrades on real user typos, and why "offline
metric went up, online metric didn't" is a well-known result in search and recommender teams.
The habit: before trusting a number, ask *"who generates the inputs in production, and is that
who my harness is imitating?"*

**Docs:** [Wikipedia — dataset shift](https://en.wikipedia.org/wiki/Dataset_shift)
