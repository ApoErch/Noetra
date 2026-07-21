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
