# Concepts — problems we hit, decisions we made

Written to be read before an interview. Two sections:

- **A. Problems encountered** — each entry: what broke, *why* it broke, what the failure is
  called in general (so it can be looked up), and how we solved it.
- **B. Design decisions** — each entry: what we chose, the real alternative, why ours wins
  *here*, and what would change the answer.

Per-milestone narrative lives in `LEARNING_LOG.md`; this file is the index of individual
stories. Entries are in the order they happened.

---

## A. Problems encountered

### A1. The host's `.venv` leaked into the Linux containers

**Problem:** `api` and `worker` both crashed on startup fighting over `/backend/.venv`
(`Directory not empty`); later, `uv add` on Windows broke on Linux symlinks (`lib64 -> lib`).
**Why:** `./backend:/backend` is bind-mounted for live reload, so the host's Windows-built
`.venv` shadowed the image's Linux venv. Both containers then tried to rebuild it at once.
**Name:** bind-mount shadowing; host/container toolchain mismatch.
**Fix:** an anonymous volume at `/backend/.venv` on both services. Docker layers the more
specific mount on top, so each container keeps a private Linux venv while the source is
still bind-mounted. Host `.venv` (mypy, ruff, `uv add`) and container `.venv` (what runs)
are deliberately separate.

### A2. GitHub's `repo` scope has no read-only variant

**Problem:** Noetra only ever reads code, but the token it stores can push to the user's repos.
**Why:** classic OAuth Apps expose one scope for private-repo *contents* — `repo`, read+write.
Read-only "Contents" permissions exist only on GitHub Apps, a different integration model
(installation tokens, different registration flow).
**Name:** least privilege violated at the *credential* level, not the *behaviour* level.
**Fix:** accepted and documented. Safe because V1 has no write-capable code path at all. The
moment a write feature is added, migrate to a GitHub App — this is the recorded trigger.

### A3. `Bearer` auth works on api.github.com and silently fails on git clone

**Problem:** `git clone` with `Authorization: Bearer <token>` failed with "could not read
Username", as if no credentials were sent — the same token worked fine against the REST API.
**Why:** github.com's git-over-HTTPS server and its REST API are different systems with
different auth conventions. The git server only understands HTTP **Basic**; `Bearer` is not a
scheme it checks for, so the request looks anonymous.
**Name:** one host, two auth surfaces.
**Fix:** `Authorization: Basic base64("x-access-token:<token>")` for clones (`worker/tasks.py`);
`Bearer` stays for REST (`core/github.py`). Same token, two header formats.

### A4. The clone header — and therefore the token — got persisted into `.git/config`

**Problem:** passing the token as `git -c http.extraHeader=... clone` was chosen precisely so
it would never touch disk (unlike a token-in-URL). Reading `dest/.git/config` afterwards
showed the header sitting there under `[http]`.
**Why:** `-c` is process-local for most git commands, but `git clone` must persist some config
into the new repo (remote URL, tracking branch) and it carries `http.*` overrides along with
it — sensible for `http.postBuffer`, dangerous for a credential.
**Name:** credential leakage through persisted configuration; "verify empirically, don't trust
the general rule for the specific command".
**Fix:** `git config --unset-all http.extraHeader` inside the destination immediately after a
successful clone. The eval seed avoids it entirely by using `-c` on `fetch`, not `clone`.

### A5. `import worker` worked for `uvicorn` and crashed for `celery`

**Problem:** the worker died on start with `ModuleNotFoundError: No module named 'worker'`,
same working directory and same files as the API, which imported everything fine.
**Why:** `sys.path` depends on *how* Python is started. Console-script entry points
(`.venv/bin/uvicorn`, `.venv/bin/celery`) do not add the cwd. `uvicorn` special-cases this
when resolving `api.main:app`; `celery` does not.
**Name:** implicit vs explicit import roots; relying on one tool's convenience behaviour.
**Fix:** `PYTHONPATH=/backend` set explicitly on both services in `docker-compose.yml`.

### A6. Real repos broke "every tracked path is a readable text file"

**Problem:** importing `fbsamples/f8app` crashed the bulk `INSERT`; importing the Linux kernel
raised `IsADirectoryError`. Small test repos never showed either.
**Why:** (1) NUL bytes are valid UTF-8, so `.decode()` succeeds — but Postgres `text` rejects
`\x00`. (2) `git ls-files` lists symlinks like files; a symlink's tracked *content* is the
target path string, and reading through one that points at a directory crashes.
**Name:** input-assumption failures that only appear at real-world scale.
**Fix:** check raw bytes for `\x00` before decoding; `Path.is_symlink()` → store
`Path.readlink()` (what git itself considers the content). Plus a 1 MB per-file cap and a
top-level `try/except` so a crash marks the repo `failed` instead of leaving it stuck at
`cloning` forever.

### A7. A caught exception's `str()` would have written the token into the database

**Problem:** adding a clone timeout meant catching `subprocess.TimeoutExpired`. Its default
string form contains the full command list — including the Basic-auth header.
`repo.error_message = str(exc)` would have shown a live token in the UI.
**Why:** exception messages routinely embed their inputs (argv, headers, request bodies).
**Name:** secrets leaking via logs/error messages.
**Fix:** catch `TimeoutExpired` in its own `except` before the generic one and hand-write the
message (`"Clone timed out after Ns"`).

### A8. Two clicks on Retry raced two clones of the same repo

**Problem:** `retry_repository` re-enqueued `clone_repository` on every call; two fast clicks
both saw `status == FAILED` and both started `rmtree` + clone on the same directory.
**Why:** check-then-act across two processes (`api` enqueues, `worker` runs) with no shared
lock. A Python lock is per-process; `api` and `worker` are separate containers.
**Name:** race condition; the fix is a distributed lock — more precisely a **lease**
(self-expiring lock).
**Fix:** Redis `SET lock:index:{repo_id} 1 NX EX 600` (`core/redis_client.py`) — atomic
check-and-set in one round trip; TTL is a crash-only safety net well above the 300 s clone
timeout; released in the worker's `finally`. A second attempt gets `False` → 409. Known
sharp edge left unbuilt: no fencing token, because no task can outlive the TTL yet.
The *other* race (two simultaneous imports of one URL) was already closed by a DB unique
constraint — a permanent uniqueness rule belongs in Postgres, not re-implemented in Redis.

### A9. Adding an enum column to an existing table: `type "file_language" does not exist`

**Problem:** the same `sa.Enum(...)` that worked in `CREATE TABLE` failed in
`op.add_column` on `files`.
**Why:** SQLAlchemy creates the Postgres enum type as part of building a table from scratch;
a bare `ALTER TABLE ... ADD COLUMN` doesn't infer "and create this type first".
**Name:** Alembic/Postgres enum gotcha.
**Fix:** `postgresql.ENUM(...).create(bind, checkfirst=True)` first, then the column with
`create_type=False`; mirror on downgrade. Nothing needed manual cleanup after the failure
because Postgres DDL is **transactional** — the whole migration rolled back (MySQL would have
left a half-applied schema).

### A10. `alembic --autogenerate` proposed dropping two working indexes

**Problem:** the migration after the GIN indexes generated `drop_index` for both.
**Why:** those indexes were `op.execute("CREATE INDEX ... gin_trgm_ops")` — raw SQL, because
`Index()` can't express the operator class — so they don't exist in `Base.metadata`.
Autogenerate diffs the live DB against metadata; an index it can't see looks "removed".
**Name:** metadata-diff blind spot.
**Fix:** hand-strip the drops; standing rule: **read every generated migration's `upgrade()`
and `downgrade()` before applying it**.

### A11. Import resolution took ~8 minutes on TensorFlow

**Problem:** the graphing stage on a 36k-file repo stalled for minutes.
**Why:** each absolute Python import scanned every known path for a suffix match —
O(imports × files), tens of millions of string comparisons over data that never changes
between scans.
**Name:** repeated linear scan → precomputed index (the time/space trade-off behind every
hash map, DB index, and symbol table).
**Fix:** `build_suffix_index` once per repo (every path suffix → matching paths), then O(1)
lookups. ~10,000× faster at that scale, identical output.

### A12. Deleting a repo left its clone on disk forever

**Problem:** DB rows cascaded away; `/data/repos/{id}` stayed, growing unbounded.
**Why:** nothing told the worker's volume. And the obvious fix — `shutil.rmtree` in the
`DELETE` endpoint — violates the project rule that `api` never does slow, repo-touching work.
**Name:** deferred cleanup / eventually-consistent side effects.
**Fix:** the endpoint deletes the authoritative record and returns; it enqueues
`worker.tasks.delete_repository_clone` to reclaim disk whenever the worker gets to it.
Nothing reads the directory once the row is gone, so the delay is harmless.

### A13. Conceptual questions scored exactly 0.00 recall

**Problem:** the first eval baseline: symbol 1.00, keyword 0.79, conceptual **0.00**. Not
ranked badly — an empty result list.
**Why:** `websearch_to_tsquery` joins bare terms with **AND**. A natural-language question
demands every surviving word in one document; almost none contain all of them.
**Name:** query relaxation (Elasticsearch's `minimum_should_match`; Postgres has no equivalent).
**Fix:** strict pass first, then an OR-rewrite backfilling unused slots. Strict hits keep
their positions, so precision cannot regress — the eval proved it (symbol/keyword byte-identical,
conceptual moved). Honest footnote: this was only half the story — the target file often
uses different vocabulary entirely (`encrypt`/`token` vs "credentials protected"), which is
the semantic leg's job.

### A14. Retrieval found the right file, then cited line 1

**Problem:** 6 of 9 remaining misses were "right file, wrong line".
**Why:** the index matched on Postgres **lexemes** (stems: `credenti`) while `_best_line`
matched literal words ("credentials"). A stem-only match scored zero on every line and fell
back to line 1.
**Name:** vocabulary drift — never reimplement a component's normalization; ask it what it did.
**Fix:** `tsvector_to_array(to_tsvector(...))` returns the engine's own lexemes; match by
stem prefix, score lines with their neighbours. `@20` 0.69 → 0.79. Then chunk-level retrieval
made `_best_line` irrelevant entirely — a chunk knows its own line range.

### A15. Switching to chunks *regressed* `recall@20` (0.79 → 0.74)

**Problem:** 20 slots used to mean 20 files; with chunks it meant 20 chunks from 10 files.
`docs/CONCEPTS.md` alone took 8 of 20.
**Why:** arithmetic, not ranking — one strong document crowds everything else out.
**Name:** result diversity / "collapsing" (Elasticsearch `collapse`; MMR is the general form).
**Fix:** `row_number() OVER (PARTITION BY file_id ORDER BY rank DESC)` with a cap of 2 per
file — in SQL, in a subquery, because `LIMIT 20` would already have discarded the other files
before Python could cap anything. Re-applied once more *after* fusion, since two legs each at
their cap can still put one file in 4 slots.

### A16. Prose about code outranked the code (`LICENSE` beat the implementation)

**Problem:** for natural-language queries, noetra's entire top-20 was `docs/*.md`; requests
returned `HISTORY.md` and `LICENSE` ahead of the module.
**Why:** the `english` text-search config is built for English prose, so it scores writing
*about* code higher than code. Relevance and importance are different things; a scorer only
knows the first.
**Name:** query-time boosting.
**Fix:** `ts_rank × 0.3` where `file.language IS NULL` (exactly the non-py/js/ts set). Docs
stay reachable, just below code. Conceptual `@5` 0.14 → 0.36. Chunking + A15 + A16 together:
`@5` 0.60 → 0.76.

### A17. The eval couldn't referee the fix in A16

**Problem:** every one of the 42 original answers lived in a source file. Demoting non-source
files could only ever raise the score — a factor of 0.001 would have "scored better" while
making documentation unreachable.
**Why:** a benchmark can only judge a change its answer key is neutral about.
**Name:** construct validity / benchmark gaming / Goodhart's law.
**Fix:** a fourth question kind, `docs` — 4 questions whose answers genuinely live in prose,
as a guard-rail (it reports `@5 0.75 / @20 1.00`, confirming 0.3 demotes without burying).
Kept as a *separate kind* so the original buckets' denominators stayed comparable to every
earlier run. Habit: before trusting a number, ask "could this go up while the product gets
worse?"

### A18. The Celery worker kept running old pipeline code while the API auto-reloaded

**Problem:** after adding the chunking stage, search silently returned nothing.
**Why:** `uvicorn --reload` picked up the new reader (`search()` over `chunks`); the
long-running Celery worker has no auto-reload and kept executing the writer it loaded at
startup — which never wrote chunks.
**Name:** reader/writer version skew between processes.
**Fix:** restart the worker after any pipeline change; treat "search returns nothing after a
pipeline edit" as this first.

### A19. Structural (trigram symbol-name) retrieval was worth +0.04 — so it was deleted

**Problem:** the second retriever cost a module, a `pg_trgm` index, extra fusion code, and
frontend fields. Was it earning its keep?
**Why:** AST chunking means a function's own definition line is usually the top *lexical*
hit for its name already. The ablation (46 questions, in vs out) showed `@5` 0.76 vs 0.72,
every point from one `$`-prefixed identifier the tokenizer mangles.
**Name:** ablation; "no retriever joins the fusion without a `recall@k` movement that
justifies it" — a rule that cuts as readily as it admits.
**Fix:** removed the module, the enum slot, the frontend fields, and the index (own migration);
re-ran the eval to confirm 0.72/0.78 reproduced exactly. Noted precisely: this was
symbol-**name** lookup, not graph traversal (M8), which is different data and unaffected.

### A20. Free-tier quotas and a silent fallback made M6 unmeasurable

**Problem:** the semantic leg was built against Gemini's free tier. Mid-eval the run hit a
429 that no backoff recovered; a "preliminary" number (conceptual +0.07) turned out to be
untrustworthy because `search()` silently degraded to lexical-only whenever the embedding
call failed — the run *looked* normal.
**Why:** three things stacked. (1) Two separate quotas on one endpoint — 100 req/min *and*
1,000 req/day — distinguishable only by the error's `quotaId`; the per-minute retry logic
was useless against the daily one. (2) Growing machinery to work around a free tier (SDK
retry tuning, token-budget batching, inter-page sleeps, 9-attempt backoff) — real engineering
spent on a constraint that $0.02/M tokens removes. (3) A degraded path with no log line is
indistinguishable from the healthy path.
**Name:** pacing vs backoff (avoid vs react); observability of degraded modes; build-vs-buy.
**Fix:** switched to OpenAI `text-embedding-3-small` (paid, high limits, native 1536 dims,
provider-normalized vectors — which also deleted the client-side L2 normalization and
asymmetric `task_type` handling Gemini needed). Deleted the pacing/batching/retry code
outright rather than parameterising it; `core/ai` is ~40 lines and the provider is a config
value. The silent `except: pass` became `logger.warning(...)`, so an eval run shows whether
semantic actually ran. The measurement is redone from scratch on the new provider — a
provider switch changes the embedding space, so old vectors are wiped, not mixed.

### A21. GitHub returned 503 for authenticated requests with no `User-Agent`

**Problem:** the OAuth callback's profile fetch failed with a `503`, not a `401`/`403`.
**Why:** GitHub's REST API requires a `User-Agent` on every request and answers its absence
with an opaque 503.
**Fix:** set the header in `core/github.py`; traced by comparing an unauthenticated request
(clean 401) against the authenticated one inside the container, then confirming in the docs.

### A22. Fusion scored *worse* than either leg alone

**Problem:** the first clean M6 run on noetra: lexical `@5 0.81 / @20 0.94`, semantic
`0.88 / 0.88`, **fused `0.81 / 0.88`** — worse than each leg on the metric that leg was
good at. Dumping the top-20 for one conceptual question showed lexical had the answer at #4,
semantic at #2, and the fused list had it *nowhere* — all 20 fused hits were tagged
"found by both legs".
**Why:** RRF scores `Σ 1/(k + rank)`. With the paper's `k=60` and each leg handing fusion 40
candidates, `1/(60+2) = 0.016` for a #2 in one leg loses to `2/(60+40) = 0.020` for something
at rank 40 in *both* — rank is nearly flat at that `k`, so "appears in both lists at all"
becomes the dominant signal. On a 248-chunk repo the two legs share most of their mediocre
tail (`models.py`, `alembic.ini`, migrations), and that agreed-upon junk crowded out each
leg's best hit. `k=60` was tuned on TREC runs 1,000 deep, where rank still carries signal at
that depth; on 20-deep lists it doesn't.
**Name:** RRF on short lists; the fusion pool being wider than the signal.
**How we solved it:** a 6-config sweep on the harness (fetch depth 20/40 × `k` 60/10/1),
before touching code. The knobs interact: shrinking the fetch alone fixed `@20`; lowering
`k` alone made `@5` *worse* (40 candidates + small `k` lets deep single-leg junk in).
Together — **fetch `limit` per leg, `k=10`** — fusion ties the best single leg on both
`@5` (0.88) and `@20` (0.94) and covers each leg's weak bucket (keyword back to 1.00, docs
back to 1.00). `k<10` buys nothing. One conceptual hit semantic finds at #2 still doesn't
survive fusion — a real limit of rank-only fusion over two legs, not a tuning miss.
On the full 46 the re-tune held (fused 0.72 → 0.78 `@5`) — but it also exposed the bigger
fact the 16-question run had hidden: **semantic alone scores 0.85**, so equal-vote fusion was
a net −0.07, concentrated in `conceptual`. RRF is a vote; with equal weights lexical (0.36
on conceptual alone) could out-vote semantic (0.64) whenever their junk overlapped. Yet
semantic-only *loses* docs (1.00 → 0.75) and keyword `@20` (1.00 → 0.93) — questions only
lexical gets. **Weighted RRF, semantic 2×**, resolved it: 0.85 / 0.91, semantic's `@5` plus
lexical's coverage, beating both legs alone. At 3× the result is byte-identical to
semantic-only — the weight where a leg stops mattering is itself measurable. Standing
caveat: the eval feeds raw English, which flatters semantic; the agent will send
identifier-shaped queries where the two legs tie. Numbers in `RETRIEVAL.md`.

### A23. The graph leg couldn't be measured before the agent existed

**Problem:** M7 was planned as a call graph fused into RRF as a third leg, ending with the
same in/out `recall@k` ablation that cut A19. Pressed on *why* fusion, the design didn't
hold: graph traversal takes a location and returns connected locations — that's
reachability, not an estimate of query relevance, which is the one thing RRF assumes each
leg provides. And a leg seeded from the other two isn't independent; it can only amplify
their vote. Once the graph stops being a fused leg there is no `recall@k` to ablate — the
only place its value shows up is inside the agent loop, and the agent didn't exist yet.
**Why:** the build order had been written leg-by-leg ("each leg ends with a measured delta")
and the graph got slotted in as a leg because that was the shape of the sentence, not
because it was one.
**Name:** measure-then-buy applied to milestone order; the LocAgent-style tools-on/off
ablation.
**How we solved it:** checked what the field does before rewriting anything — LARGER,
RepoGraph, LocAgent, CodexGraph, Codebase-Memory, GraphRAG-Bench, CodeCompass, plus
Sourcegraph, Augment, Greptile, Aider, Claude Code. Nobody fuses graph neighbours into a
ranked list (B20). Swapped the milestones: the agent ships first with the tools that already
exist (`code_search`, `read_file`, `list_dependencies`), producing an agent eval; the call
graph lands after it as `get_callees`/`get_callers` and is measured on that eval with the
tools on vs. off, plus an edge-quality eval of the resolved graph itself.

---

### A24. `read_file` crashed on every file ending in a newline — and the agent burned its whole budget on it

**Problem:** the first smoke run of the agent scored 3/3 cited but took 8 tool calls per
question. The trace showed `read_file` failing six times in a row with `list index out of
range`; the model kept retrying with different line ranges, then answered from the search
snippets alone. The line count was computed as `content.count("\n") + 1`, which is one more
than `splitlines()` returns for a file that ends in a newline — i.e. nearly every file.
**Why:** the tools node catches every exception and hands the message back to the model as
text (so a bad argument can't kill a turn). Correct design, but it turned a crash into a
silent budget drain — the eval's `calls/question` column is what made it visible, not an
error log.
**Name:** off-by-one on trailing newline; the "swallowed error becomes wasted tool calls"
failure mode that makes per-question cost a first-class eval metric.
**How we solved it:** count lines with `len(splitlines())` everywhere; added a unit test on
a trailing-newline file. Calls per symbol question went 7.0 → 2.0 and tokens 14k → 5k.

### A25. Without the repo map, the model answered from memory with zero tool calls

**Problem:** the repo-map ablation on gpt-5.4-mini lost one question — not to a retrieval
miss but to the model answering "git clone basic auth header" from its own knowledge,
making **no tool calls** and citing nothing. With the map in the prompt it searched, read,
and cited correctly. On gpt-4.1-mini the map made no measurable difference either way.
**Why:** a system prompt that only *describes* tools is easy to skip when the model already
"knows" the answer; a concrete listing of the repo's files and symbols anchors it to *this*
codebase and makes "look it up" the obvious move.
**Name:** grounding pressure — the map's value is behavioural, not recall.
**How we solved it:** kept the map (1,500 tokens/turn). The agent eval now reports tool
calls per question, so a zero-call answer to a code question is visible as a miss.
**Honest cost note (46 questions):** the map added +0.04 cited — two questions, inside the
noise — and the raw token column nearly doubled (6.7k → 11.6k/question) because the prompt
is re-sent on every model call. Those are cached-prefix tokens (same wall-clock either way),
but the eval doesn't yet split cached from uncached input, so the real price is unmeasured.
Open follow-ups: cents/question from `usage_metadata`, and an 800-token map.

### A26. The answer keys are narrower than the truth — three of four "misses" were correct answers

**Problem:** on all 46 questions gpt-5.4-mini scored 42 cited. Reading the four failures
against the code: zod's "attach descriptive information without altering the schema" was
answered with `.describe()`/`.meta()` (which clone and register) while the key pointed at
`registries.ts`; "how is a schema's shape copied" was answered with `extend()`'s spread
copy while the key listed two helpers 200 lines earlier in the same file; "how do I attach
metadata" cited lines 81–105 of the same `metadata.mdx` the key pins at 13–35. Only
"Transfer-Encoding chunked" was a real miss (docs + tests cited instead of `prepare_body`).
**Why:** the keys were written by one person on 2026-07-26 by reading each repo at its
pinned SHA and recording *the* location — one per question — and were validated only for
path existence, never reviewed for completeness. A one-location key makes the score a lower
bound: any correct answer elsewhere counts as a miss.
**Name:** answer-key coverage; scores as lower bounds.
**How we solved it:** not yet fixed (documented 2026-09-05). Fix is to add the verified
alternative locations as extra `answers` entries — the YAML already allows several — and
re-score the stored JSONL answers offline, no API cost. Guard against fitting the key to the
model: only add a location after reading it and confirming it answers the question.

### A27. A single 884,000-character line blew past the model's request limit

**Problem:** on the requests repo one `read_file` call returned `ext/requests-logo.svg` —
one line, 884k characters ≈ 220k tokens — and OpenAI rejected the request (429, "request
too large"), killing the whole paid eval run. The 200-line cap didn't help: lines are not a
safe unit when one line is a minified bundle or an inline SVG.
**Why:** the cap was written thinking in source-file terms; search surfaced the SVG chunk
because lexical matching doesn't care what a file is.
**Name:** cap by bytes, not by lines; make paid runs resumable and per-item fault-tolerant.
**How we solved it:** `read_file` now also caps at 12,000 characters and search snippets at
160; the eval records a failed question instead of aborting, and its per-question JSONL
checkpoint meant the restart re-used all 16 finished answers.

## B. Design decisions

### B1. FastAPI over Flask / Django
**Chose:** FastAPI + Pydantic v2. **Alternative:** Flask (minimal, sync) or Django (batteries
included, ORM, admin). **Why:** validation at the HTTP boundary is a type annotation, not a
hand-written check; the OpenAPI schema falls out for free (frontend types are generated, never
hand-maintained twice); native async matters for the SSE chat stream. Django's ORM/admin buy
nothing here — SQLAlchemy + Alembic are already the DB layer.

### B2. Postgres (+ pgvector) over MySQL, and over a separate vector database
**Chose:** one Postgres. **Alternative:** MySQL; or Postgres + Pinecone/Qdrant for vectors.
**Why:** transactional DDL (A9 — a failed migration rolls back cleanly; MySQL's doesn't);
built-in full-text search with generated columns (B9); pgvector keeps embeddings, files,
chunks, and ownership in *one* transaction and one `WHERE repository_id =` filter. A second
datastore is a second thing to sync, secure, and keep consistent — unjustified at V1 scale.

### B3. Celery + Redis over threads / RQ / cron
**Chose:** Celery workers behind a Redis broker. **Alternative:** background threads in the
API process; RQ; a cron-driven poller. **Why:** indexing is minutes of bursty work; the API
and the worker scale on different curves and must be separately deployable. Threads die with
the request process; RQ is Linux-only and thinner on retries/routing. Producer/consumer
decoupling is `send_task` by *name* — `api` never imports `worker`.

### B4. Redis as broker and lock, not as result backend
**Chose:** Celery broker + per-repo lease (A8). **Alternative:** Celery's Redis result backend
for job status. **Why:** indexing status must be queryable and joined to ownership — it's a
domain field (`Repository.status` in Postgres), not a task return value keyed by task id.

### B5. Signed-cookie sessions over server-side sessions / JWT
**Chose:** Starlette `SessionMiddleware` (`itsdangerous`) holding `{"user_id"}`. **Alternative:**
a sessions table or Redis store; JWTs. **Why:** the API stays stateless with no extra lookup
per request; the cookie is *signed* (tamper-evident) not encrypted — fine because `user_id`
isn't secret. JWT is the same idea with more ceremony and no revocation story. Logout is
local only — OAuth 2.0 has no logout endpoint (that's an OIDC feature GitHub doesn't implement).

### B6. Encrypt (Fernet) the stored GitHub token; never hash it
**Chose:** symmetric encryption, key in env. **Alternative:** hashing, as for passwords.
**Why:** we need the *original* token back to clone on the user's behalf; hashing is one-way.
Standard for any app storing third-party OAuth tokens.

### B7. Ownership inside the query; 404 over 403
**Chose:** `WHERE id = :id AND user_id = :me` in one query, 404 on no row. **Alternative:**
fetch by id, then check owner, 403 on mismatch. **Why:** the fused query cannot forget the
check (the standard IDOR fix), and 404 doesn't leak that the id exists. The client is never
a trust boundary — a missing button is not authorization.

### B8. Tree-sitter over per-language parsers
**Chose:** one parser API, one grammar package per language. **Alternative:** Python's `ast`
plus Babel/TypeScript's compiler for JS/TS. **Why:** three languages behind one code path;
error-tolerant (a syntax error elsewhere in a file doesn't kill the parse); syntactic only,
which is *why* import and call resolution are separate steps on top. Gotcha: TS and TSX ship
as two grammars in one package.

### B9. Postgres full-text search over Elasticsearch
**Chose:** a generated `tsvector` column + GIN index. **Alternative:** Elasticsearch/OpenSearch.
**Why:** `GENERATED ALWAYS AS (to_tsvector(...)) STORED` maintains itself — no indexing step,
no drift, one migration. Reaching for a search engine before outgrowing Postgres FTS is a
whole extra datastore for nothing at this scale.

### B10. Lexical retrieval first; embeddings last
**Chose:** build order lexical (M5) → semantic (M6) → agent (M7) → graph tools (M8). **Alternative:**
vector-first RAG. **Why:** code is mostly identifiers, and the identifier you want is usually
literally in the file — exact match wins whenever an exact match exists. Embeddings earn
their keep only where the user's words appear *nowhere* in the code — real, important, a
minority. Lexical was one migration to build and trivial to throw away; embeddings cost a
full re-embed whenever chunking changes. So: build cheap, measure, then buy the expensive one.
Also why a bare-identifier query is routed straight to lexical with no embedding call.

### B11. RAG over CAG (whole repo in a cached prompt)
**Chose:** retrieval. **Alternative:** put the entire codebase in the prompt and rely on
prefix caching. **Why:** ~1M tokens ≈ 100k LOC (many repos are 10–30× that); ~10× the
per-question cost; and the killer — cache TTLs are minutes, so N users × M repos queried
occasionally re-pay the cache write constantly. Two ideas kept: a byte-stable prompt prefix
(system → tools → repo map, no timestamps) and a small-repo fast path as a V2 seam.

### B12. AST chunking with a context prefix, over fixed windows
**Chose:** one chunk per leaf entity + gap chunks, each embedded as
`path › class › signature\n<body>`. **Alternative:** fixed 512-token windows. **Why:** the
unit you index is the unit you can cite — a chunk knows its own line range, a window splits
functions in half. The prefix is contextual retrieval for the price of an f-string: a bare
`def refresh(token)` is ambiguous; with its path and class it lands near "authentication".
The lexical index is built over the same prefixed text, so it gets the context for free.

### B13. RRF fusion; no reranker in V1
**Chose:** weighted Reciprocal Rank Fusion (`Σ w/(k + rank)`, `k=10`, semantic 2×).
**Alternative:** weighted *score* averaging; a cross-encoder reranker on top. **Why:**
`ts_rank` and cosine similarity are on incomparable scales — RRF uses only rank positions,
so nothing needs calibration; the per-leg weight sizes each leg's vote, and both `k` and the
weight were set by measurement, not taken from the paper (A22). A reranker
is the standard next stage (fuse wide, cut narrow) and is deferred, not rejected: the agent's
own ability to discard bad tool results covers it until the eval says otherwise.

### B14. Exact cosine scan; no HNSW / IVFFlat index
**Chose:** `ORDER BY embedding <=> q` over every chunk of the repo. **Alternative:** an ANN
index (HNSW is the usual pick — no training step, better recall than IVFFlat). **Why:** the
per-file cap is a window function over the whole `repository_id` partition, which forces a
full sort — the index would sit unused. And a plain HNSW index returns *global* top-k before
the `WHERE repository_id` filter applies, silently under-returning (pgvector 0.8's
`hnsw.iterative_scan` fixes it at the cost of a version pin and a `SET LOCAL`). Below roughly
10⁵ vectors per partition exact search wins on correctness and often latency — the
brute-force vs ANN crossover. "Which ANN index" was the wrong first question. Revisit when
per-repo chunk counts make the scan show up in the latency budget.

### B15. `recall@k` on SHA-pinned repos as the scoreboard
**Chose:** 46 questions with known answer locations, line-level `recall@5`/`@20`, repos pinned
to commit SHAs. **Alternative:** precision, MRR, nDCG; or "the answers feel good".
**Why:** downstream, the agent can discard a bad hit but cannot recover a file retrieval
never surfaced — a miss is unrecoverable, noise is not. Line-level next to file-level makes
"right file, wrong line" its own number. A SHA is content-addressed and immutable; a branch
moves the line numbers out from under the answer keys. The eval feeds *raw English* to
`search()`, which the agent never will (it reformulates first) — so the conceptual bucket is
honest for the search UI and pessimistic for the agent; M7 scores agent-mediated retrieval
alongside.

### B16. Agentic RAG with a hand-rolled LangGraph `StateGraph`
**Chose:** retrieval as tools the model calls in a loop; the graph (state, `call_model`,
`ToolNode`, `should_continue`) written out by hand. **Alternative:** single-shot RAG; or
`create_react_agent`. **Why:** code questions vary from "one lookup" to "search, read, follow
a caller, search again" — a fixed pre-LLM retrieval step can't adapt per query. Single-shot
would be deleted a week after the tools existed. Hand-rolling is the deliberate choice so the
citation-collection node and repo-map priming are plain code, and so the loop can be
explained end to end. Citations come from tool-result metadata, never from the model.

### B17. ~~Seeded graph expansion as a third fusion leg (accepted coupling)~~ — superseded by B20
**Chose (then):** walk `reference_edge ∪ dependency_edge` outward from the top lexical +
semantic hits and fuse the result in, accepting that a wrong seed gets reinforced rather than
cancelled. **Reversed 2026-09-04** before any code: the "traversal has no notion of query
relevance" sentence in the rationale was the argument *against* fusing it, not for. Kept
here because the reversal is the interesting part — see A23 and B20.

### B18. Paid OpenAI embeddings over Gemini's free tier
**Chose:** `text-embedding-3-small`, `EMBEDDING_PROVIDER` + one key as the whole switch.
**Alternative:** keep the free tier and keep engineering around its quotas. **Why:** A20 —
the quota machinery was outgrowing the feature, and a silent fallback had already produced
one untrustworthy number. At ~$0.02 per million tokens the cost argument is gone; native
1536 dims and provider-normalized vectors also removed two correctness footguns. Embeddings
stay single-provider on purpose: switching providers means a full re-embed regardless, so a
live switch has no use case.

### B19. One EC2 host + Docker Compose, provisioned by Terraform
**Chose:** the same Compose stack as local dev on a single EC2 instance, every AWS resource
declared in Terraform. **Alternative:** ECS Fargate services for `api`/`worker` with RDS and
ElastiCache; EKS. **Why:** zero drift between dev and prod (same `docker-compose.yml`), one
bill, one box to reason about — and V1 load (a handful of users, bursty indexing) doesn't
need `api` and `worker` scaling independently yet. Terraform over console clicks: reviewable,
reproducible, destroyable. What changes the answer: worker queue depth or API latency that
one instance can't absorb — at that point split the worker into its own instance/service and
move Postgres to RDS first (it's the only durable state).

### B20. The call graph is agent tools, not an RRF leg — and ships after the agent
**Chose:** fusion stays lexical + semantic. The graph is exposed as `list_dependencies`,
`get_callees`, `get_callers` — one hop per call, each edge carrying a resolution confidence
(same file 0.9 → imported file 0.85 → unique name 0.7 → ambiguous 0.3) so tools can filter
the ambiguous tail. The agent, not a fixed pipeline, decides when to hop. **Alternative:**
the B17 seeded third leg; or a LARGER-style sidecar that attaches 1-hop neighbours to each
`/search` hit (deferred until the agent eval shows a need). **Why:** RRF combines independent
relevance estimates; hop distance is a property of the seed, not the query, and a seeded leg
can only amplify the other two. Whether a neighbour matters depends on the question — zero
hops for "where is X defined", several for "how does auth flow" — which only an agent can
judge per query. The field agrees: LARGER attaches neighbours to the anchoring hit and never
re-ranks; RepoGraph found 1-hop best and 2-hop worst; LocAgent's `TraverseGraph` tool is
worth +4 pts next to keyword search's +13; Augment keeps its call graph as a "structural
reachability" index beside BM25 and vectors; GraphRAG-Bench shows graphs *lose* on simple
lookups. Honest scope: keyword search already finds a name's call sites, so `get_callers`
mostly adds the enclosing caller; `get_callees` is the genuinely new capability. **Order:**
agent first (M7) with the tools that already exist, because the graph's value can only be
measured inside the loop — tools on vs. off on the agent eval (M8). What changes the answer:
the agent eval showing the conceptual bucket still failing on multi-hop questions with the
tools present — that's the trigger for the sidecar, or for a real reranker.

### B21. Simple loop + mechanical guards, over router and grader nodes
**Chose:** one `call_model ↔ tools` loop; off-topic handled by a system-prompt rule; empty
results return a steering message; an identical repeat call is blocked; a hard tool budget
forces an answer; a regex citation verifier runs before `END`. No extra LLM calls per turn.
**Alternative:** the original M7 sketch — a router node (on/off-topic classifier) before the
loop and a CRAG-style grader node after every tool call. **Why:** in a tool-calling loop the
model already sees each result and decides whether to search again, so a grader re-decides
what the next `call_model` decides anyway; and both router branches end in the same model
call. arXiv 2608.01507 (repo-level code QA, 4 models × 15 repos): plain search+read loop
65 % pass vs. orchestrator/sub-agents 46 %, at half the cost per correct answer, with tool
calls beyond need correlating negatively with correctness. Cursor, Claude Code, Copilot and
SWE-grep all run the plain loop. **Revisit triggers:** router — the eval or real use shows
tool calls on off-topic questions or refusals on on-topic ones; grader — many turns with
`retrieved` true but `cited` false (the model saw the answer and still re-searched or
answered from junk); reranker — retrieval `recall@5` stalls while `@20` stays high;
retrieval sub-agent — main-model context blowing up on large repos. Each is a small graph
change measurable in a day with the `eval.agent` flags.

### B22. Own `chat_conversation`/`chat_message` tables over a LangGraph checkpointer
**Chose:** persist only user questions and final assistant answers (with verified citations
and the tool trace for the UI); rebuild each turn's prompt as system + map + last 12 of those
+ the new question. **Alternative:** `PostgresSaver` keyed by `thread_id`, which snapshots
the whole graph state after every step. **Why:** the checkpointer replays every previous
turn's tool outputs — turn 3 carries turns 1–2's file reads (~12k tokens vs ~3k) — which is
exactly the "tool result clearing" Anthropic recommends against keeping; the UI's
conversation list and history are ordinary SQL on our rows, ownership-scoped like every
other read, instead of filtering checkpoint blobs; and the checkpointer's real strengths
(resume a crashed run mid-step, human-in-the-loop, time travel) aren't needed for a
few-second chat turn. `END` ends a turn, not the conversation — the next message re-invokes
the graph with the stored history. **What changes the answer:** a turn that can be
interrupted and resumed (human approval before a tool runs), which is the moment to add a
checkpointer without touching the graph code.

### B23. `gpt-5.4-mini` as the default chat model
**Chose:** `gpt-5.4-mini` ($0.75 / $4.50 per M tokens). **Alternative:** `gpt-4.1-mini`
(half the price), or the original `gpt-4o-mini`. **Why:** measured on the agent eval,
46 questions: cited 0.91 vs 0.59 for 4.1-mini, fewer tool calls (3.3 vs 3.8), and
faster (4 s vs 7 s per question). Per question that is ~1 ¢ vs ~0.5 ¢. Caveat: most of
4.1-mini's gap is citation *format* — it found the code but wrote "lines 90-99" instead of
`[path:90-99]`, which the verifier rejects. Still a product failure (no clickable link),
but a fairer comparison needs the verifier to accept bare `path:a-b` first. Config-only switch
(`OPENAI_CHAT_MODEL`); `--model` on `eval.agent` re-runs the comparison.
