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

## Milestone 3 — Import + clone

**Built:** `POST /api/v1/repos` validates a GitHub URL, blocks duplicate imports, persists a `queued` `Repository` row, and enqueues `worker.tasks.clone_repository` by name over Celery/Redis. The worker task authenticates as the owning user (their decrypted OAuth token), shallow-clones the repo into a Docker volume, and drives status through `queued → cloning → ready | failed`, capturing GitHub's real error text on failure.

**Core concept(s):** git's two separate authentication surfaces — the REST/GraphQL API (`Bearer` tokens) vs. the git-over-HTTPS wire protocol (HTTP Basic auth only) — and why a token that works against one silently fails against the other. Credential injection via `-c http.extraHeader` instead of embedding a token in the clone URL, including the gotcha that `git clone` (unlike most git commands) persists `-c http.*` settings into the new repo's `.git/config`, so the header has to be explicitly stripped afterward. Celery producer/consumer decoupling (`send_task` by name, no import of `worker` from `api`). Why a background worker process needs its own explicit `PYTHONPATH`/`sys.path` setup — it isn't a website server, so tooling that quietly fixes this for HTTP frameworks (`uvicorn`) doesn't help it.

**Recruiter-ready explanation:** Importing a repo is a two-phase handoff: the API validates the request, writes a `queued` row, and immediately returns — the actual clone (which can take real time and hit the network) runs in a separate background worker process picked up off a queue, so no HTTP request thread ever blocks on it. That worker authenticates the clone with the user's own GitHub token, using an HTTP header instead of putting the token in the clone URL, specifically so the credential never ends up saved in plaintext inside the cloned repo's own config file — and after discovering that git clone actually copies some of those header settings into the new repo's config by default, an explicit cleanup step strips it right after the clone finishes. Every outcome (success or failure, with GitHub's actual error message) is written back to the database as a status field, so the frontend always has an accurate, pollable answer to "what's happening with my import."

**Tricky part:** Two separate, non-obvious auth/persistence gotchas stacked on each other. First, `Authorization: Bearer <token>` (which works fine against GitHub's REST API and was assumed to work everywhere) silently fails against git's own HTTPS server with a confusing "could not read Username" error — that server predates Bearer conventions and only speaks HTTP Basic auth. Second, after switching to Basic auth via `-c http.extraHeader`, a manual `.git/config` inspection revealed the header had been written to disk anyway — `git clone` specifically (not `git` commands in general) persists certain `-c` overrides into the resulting repo's local config, silently reintroducing the exact leak the header approach was meant to avoid. Both were only caught by physically running the clone and reading the resulting files/errors, not by reasoning about the docs alone.

## Milestone 4 — Parse + extract

**Built:** Tree-sitter parsing over every Python/JavaScript/TypeScript file (including `.jsx`/`.tsx`), producing a real symbol table (`code_entities`: functions, classes, methods) and a resolved import graph (`dependency_edges`) — plus a Postgres full-text index that comes along for free. All wired into the indexing pipeline: `cloning → parsing → graphing`, no AI calls anywhere in this milestone.

**Core concept(s):** Tree-sitter as a language-agnostic parser (one API, many grammars) producing a concrete syntax tree, vs. a language-specific tool like Python's `ast`. The `indexer` package as pure, DB-free, HTTP-free logic (text in, structured data out) — unit-testable without infrastructure, and reusable regardless of which pipeline stage calls it. Import resolution as a two-layer problem: extracting the literal module string is pure syntax (parsing's job), but turning `.utils` or `./foo` into an actual file in *this* repo requires knowing the whole repo's file list (a separate, graphing-stage job).

**Recruiter-ready explanation:** Before this milestone, a codebase in Noetra was just raw text rows in a database — great for keyword search, useless for "where is `createToken` defined?" Tree-sitter parses each file into a real syntax tree (the same technology GitHub uses for code navigation), and a pure extractor walks that tree pulling out every function, class, and method into its own database row with exact line numbers — that's the symbol table. The same pass also pulls out every import statement, and a second, separate step resolves those imports to actual files elsewhere in the repo (handling both `import package.module` absolute-style imports and `from ./utils import x`-style relative imports, including repos that put their code under a `src/` folder). That import graph is what lets a future chat agent answer "what does this file depend on?" without re-parsing anything. Everything in this milestone runs locally and instantly — no network calls, no cost — which is the whole point of doing it before the expensive embedding stage.

**Tricky part:** Two gotchas that only showed up by actually running things, not by reading docs. First, Postgres enum columns added via `ALTER TABLE` to an *existing* table don't auto-create their own type the way a brand-new `CREATE TABLE` does — the migration failed with `type "file_language" does not exist` until the type was created explicitly first. Second, Alembic's `--autogenerate` can't see indexes that were hand-written as raw SQL (needed here for GIN's non-default operator classes) in its model diff — the very next migration silently proposed *dropping* those real, working indexes because they looked "unaccounted for." Both were only caught by reading the generated migration file line-by-line before running it, and by testing the actual failure/rollback in a live container rather than assuming the migration would apply cleanly.

## Milestone 5 — Retrieval eval + hybrid search

**Built:** A retrieval eval harness (46 pinned questions across 3 repos frozen at commit SHAs, scored by `recall@k`), AST-aware chunking as a new pipeline stage, and a search UI that returns real `file:line` citations and opens the cited code as an overlay. Using the harness on its own output took line-level `recall@5` from **0.60 to 0.76** and `recall@20` from **0.62 to 0.81** — with no AI, no embeddings, and no new services.

**Core concept(s):** `recall@k` as the scoreboard — the share of questions with a correct location somewhere in the top *k* — and why recall rather than precision (a reranker and the model can both discard a bad hit; neither can recover a file retrieval never surfaced). **AST chunking**: the unit you index is the unit you can cite, so chunking on syntax boundaries is what makes a citation exact rather than guessed. And **measurement validity** — a benchmark can only judge a change its answer key is neutral about.

**Recruiter-ready explanation:** Retrieval quality is the whole product, and "the results feel good" isn't a measurement — so I built a benchmark first: 46 questions whose correct answers I verified by hand, against three repositories pinned to exact commits so the line numbers can never drift. Each question is tagged by what it exercises — an identifier, a literal phrase, or a natural-language question whose words appear nowhere in the code — so the score says *which* part of retrieval is weak, not just that something is. The first run scored 0.60, and the breakdown pointed straight at two bugs in code I'd already written: Postgres was joining every word of a question with AND, so a full sentence matched nothing at all, and the code that chose which line to cite was searching for literal words while the index had stored stemmed ones, so it silently fell back to line 1. Fixing those, then switching from indexing whole files to indexing individual functions, took it to 0.76. Two of those changes initially made things *worse* in ways the benchmark caught — one file's chunks were crowding every other file out of the results, and English prose was outranking the code it described — and both fixes were only trustworthy because there was a number to check them against.

**Tricky part:** Three things worth remembering. First, one fix — demoting documentation below source code — was measured against an answer key where every correct answer *was* source code, so it could only ever look good; I added four questions whose answers genuinely live in documentation purely as a guard-rail against tuning it too far, which is a habit worth keeping whenever an optimization and a metric point the same direction. Second, chunking is independent of embeddings: it's a prerequisite for them, but it pays for itself through exact citations and smaller payloads, so the expensive milestone stayed unbuilt and still isn't justified — only 2 of 46 questions fail to surface the right file at all. Third, a genuinely confusing bug: Celery workers have no auto-reload, so after adding the chunking stage the long-running worker kept executing the version of the pipeline it had loaded at startup while the API (which does auto-reload) had already switched to reading the new table. The reader was updated and the writer wasn't, and the only symptom was a search that silently returned nothing.

**Addendum — using the harness to *delete* a feature.** After M5 shipped, I ran the ablation
the harness exists to enable: the same 46 questions with and without the second retriever
(fuzzy symbol-name lookup over the symbol table) in the fusion. It was worth `recall@5` 0.76
vs. 0.72 — real, but every point of it came from a single question in a single repo, an
identifier (`$ZodRegistry`) whose `$` the Postgres text-search tokenizer mangles. Everything
else it found, keyword search already found on its own, because AST chunking means a
function's own definition line is usually the top keyword hit for its name anyway.

So I removed it: deleted the module, simplified the entry point, dropped the now-unused
database index in its own migration, and re-ran the eval to confirm the numbers reproduced
exactly. The project's rule — *no retriever joins the fusion without a `recall@k` movement
that justifies it* — cuts both ways, and the interesting half is the second one. Most teams
only ever use a benchmark to justify adding things. The number that lets you add a feature
is the same number that lets you delete one, and deleting is where it's actually rare.

Worth being precise in interviews about *what* was cut, because the name is overloaded: this
was symbol-**name** lookup, not graph traversal. Call-graph retrieval ("who calls this?") is
a different thing on different data — it ships as agent tools in M8, after the agent, and
never as a fused leg (`CONCEPTS.md` B20).

## Milestone 6 — Semantic retrieval + provider layer

**Built:** A second retrieval leg — pgvector cosine search over the same AST-aware chunks, embedded with OpenAI `text-embedding-3-small` through a `core/ai` seam that is the only module importing any AI SDK — fused with the lexical leg via weighted Reciprocal Rank Fusion. Measured on the 46-question harness: line-level `recall@5` **0.72 → 0.85**, `recall@20` **0.78 → 0.91**; the `conceptual` bucket (questions whose words appear nowhere in the code) went 0.36 → 0.57 fused, 0.64 semantic-alone.

**Core concept(s):** **Embeddings for the vocabulary gap** — the one class of question keyword search structurally cannot answer, and why it's a minority for code. **Reciprocal Rank Fusion as a vote**, and why its two constants (`k`, per-leg weight) have to be set by measurement on *your* list depth rather than copied from the paper. **Provider seam + resumable pipeline stage** — `chunk.embedding` nullable, `WHERE embedding IS NULL`, non-fatal on provider failure, so a half-embedded repo stays searchable and a retry continues instead of restarting.

**Recruiter-ready explanation:** Keyword search finds code when the user's words are literally in it, and fails when they aren't — ask "how are credentials protected?" against a file that only says `encrypt` and `token` and it returns nothing. Embeddings fix that: each function is turned into a vector that captures meaning, so the question lands near the right code even with zero shared words. I added that as a second search leg, kept the embedding provider behind a single module so it's a config value, and merged the two result lists with a rank-based vote that doesn't need their scores to be comparable. The first honest measurement was a surprise: the merged list scored *worse* than the semantic leg alone. Digging into the top-20s showed the standard fusion constant, tuned for thousand-deep result lists, made rank nearly meaningless on my twenty-deep ones — anything both searches agreed on, even junk, beat a correct answer only one of them found. Re-tuning the constant and giving the semantic leg a double vote made the merged list beat both legs on their own, and the eval harness is what made every one of those steps a number instead of an opinion.

**Tricky part:** Three things. First, the milestone was built once on Google's free tier and could not be measured there: two separate quotas (per-minute *and* per-day) looked identical in the error, and the search code silently fell back to keyword-only when embedding failed — so a "preliminary" number was really measuring a broken run. Moving to a paid provider, deleting the pacing/batching/retry code outright, and turning the silent fallback into a logged warning is what made the measurement possible. Second, truncating input by an estimated chars-per-token ratio failed on real data: a lockfile's hashes tokenize at ~1.5 chars/token where prose is ~4, and the provider rejects over-long input with a hard 400 — the fix is to count tokens with the model's own tokenizer, not estimate. Third, the fusion result: the intuition "two retrievers merged must beat one" was wrong at the paper's default settings, and the fix came from reading the actual score arithmetic (`1/(60+2)` for a #2 in one list loses to `2/(60+40)` for something at #40 in both) rather than from turning knobs.

<!-- Add new entries above this line, most recent last -->
