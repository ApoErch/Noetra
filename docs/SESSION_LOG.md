# Session Log — handoff between sessions

Most recent entry last. Written by `/endsession`.

---

## Session — 2026-07-20

**Worked on:** Understanding M2 Auth (GitHub OAuth + session cookies) — Q&A only, no code changed.
**Done:**
- Walked the full login flow end to end: `/auth/login` → GitHub → `/auth/callback` → `get_current_user`.
- Clarified where things live: signed `user_id` in the session cookie (browser) vs. Fernet-encrypted `access_token` in the `users` table (Postgres) vs. `token_encryption_key` (`.env`, never in the DB).
- Clarified `access_token` is re-fetched and overwritten on every login (no "already have a token, skip OAuth" shortcut) — code exchange always happens, session cookie is what actually lets you skip re-login.
- Clarified `decrypt_token` is a read (safe/repeatable), not consume-once — same encrypted row gets decrypted fresh for every future repo-import operation; only the plaintext copy in memory is transient.
**In progress:** Nothing code-wise. Frontend (`web/src/App.tsx`) is still the default Vite scaffold — no login button, no `/me` call wired up yet.
**Key decisions:** None made this session — explanatory only.
**Next step:** Wire the frontend login button + `/me` check, or move on to Milestone 3 (Import + clone).
**Watch out for:** Session cookie has no explicit `max_age`/`https_only`/`same_site` set in `main.py` yet — worth revisiting before any deploy. `decrypt_token` still has zero callers in the codebase (expected — repo-import isn't built yet).

---

## Session — 2026-07-21

**Worked on:** Milestone 3 (Import + clone) — containers up, `Repository` model + migration, import endpoint. Clone itself not started.
**Done:**
- Ran `docker compose up -d --build` — all four services (`db`, `redis`, `api`, `worker`) confirmed healthy.
- Added `Repository` model + `RepositoryStatus` enum to `core/models.py`; generated and applied Alembic migration `002ecfb88441_add_repository_model`.
- Moved `worker/celery_app.py` → `core/celery_app.py` (Celery app config is shared producer/consumer infra, not worker-owned business logic); updated `docker-compose.yml`'s worker command accordingly.
- Added `parse_repo_slug()` + URL regex to `core/github.py`.
- Built `POST /api/v1/repos` (`api/repos.py`), registered in `main.py`: validates URL, blocks duplicate imports (409), inserts a `queued` `Repository` row, publishes `worker.tasks.clone_repository` via `celery_app.send_task(...)` (by name — `api` never imports `worker`).
- Verified end-to-end with a forged session cookie (signed locally with `itsdangerous`, no browser login needed): 401 unauthenticated, 422 bad URL, 201 success, 409 on repeat, row lands in Postgres as `QUEUED`, Celery message correctly published and routed to the worker (which rejected it as expected — task not defined yet).
**In progress:** `worker/tasks.py` — the actual `clone_repository` Celery task doesn't exist yet. Plan (discussed, not yet built): `git clone` authenticated via `-c http.extraHeader=...` (never embed the token in the URL — it'd persist into `.git/config`).
**Key decisions:**
- `status` is a native Postgres `ENUM`, not a plain string — DB-level rejection of invalid states.
- No `is_private` field on `Repository` — every clone will use the owning user's decrypted OAuth token regardless of visibility (an authenticated clone works fine on public repos too), so no branch/extra GitHub API call needed.
- `celery_app` lives in `core/` so both `api` (producer) and `worker` (consumer) can import it without `api` reaching into `worker`.
**Next step:** Build `worker/tasks.py::clone_repository` — clone into `/data/repos/{id}` using `http.extraHeader` token injection, update `status` through `cloning → ready|failed`, write `error_message` on failure.
**Watch out for:** Nothing blocking. The forged-cookie test trick (sign `{"user_id": ...}` with `itsdangerous.TimestampSigner` using `session_secret`) is handy for future endpoint testing without repeating the browser OAuth flow.

---

## Session — 2026-07-21 (continued)

**Worked on:** Finished Milestone 3 (Import + clone) — built, debugged, and verified `clone_repository`. Scoped (not built) the frontend login flow.
**Done:**
- Built `worker/tasks.py::clone_repository`: loads the `Repository` + owning `User`, decrypts the token, shallow-clones (`--depth 1`) into `clone_storage_dir`, moves status `queued → cloning → ready|failed` with `error_message` set on failure.
- Fixed a worker startup crash: `celery -A core.celery_app worker` never put `/backend` on `sys.path` (unlike `uvicorn`, which does this implicitly) — added explicit `PYTHONPATH=/backend` to both `api` and `worker` in `docker-compose.yml`.
- Fixed a clone auth failure: GitHub's git-over-HTTPS server only accepts HTTP **Basic** auth, not `Bearer` (that's only for the REST/GraphQL API) — task now builds `Authorization: Basic base64("x-access-token:<token>")`.
- Discovered `git clone -c http.extraHeader=...` persists that header into the new repo's `.git/config` (git clone specifically copies `-c http.*` settings into the clone, unlike most other git commands) — added `git config --unset-all http.extraHeader` right after a successful clone to close that leak.
- Verified end-to-end via the forged-cookie trick: happy path (`octocat/Hello-World`) → `ready`, files on disk, `.git/config` clean of any token; failure path (nonexistent repo) → `failed` with GitHub's real error text captured.
- Scoped the frontend login flow (not yet built): add CORS middleware to `main.py`, add `@tanstack/react-query` to `web/package.json`, wire a login button (full-page redirect to `/api/v1/auth/login`) + `/me` check + logout into `App.tsx`.
**In progress:** Frontend login flow — planned only, zero code written. `web/src/App.tsx` is still the default Vite scaffold.
**Key decisions:**
- Basic auth, not Bearer, for git clone authentication — GitHub's git wire-protocol server only understands Basic.
- Always strip `http.extraHeader` from `.git/config` right after cloning — `-c` on `git clone` isn't purely process-local the way it is on most git commands.
- Explicit `PYTHONPATH=/backend` on both backend services — don't rely on incidental per-tool behavior for basic importability.
**Next step:** Build the frontend login flow (CORS + TanStack Query + login/me/logout in `App.tsx`), then decide between Milestone 4 (Parse + extract) or a minimal repo-list UI next.
**Watch out for:** `status = READY` after `clone_repository` currently just means "cloned," not "fully indexed" — once Milestone 4+ exist this needs to become an intermediate state, not terminal. The `docs/CONCEPTS.md` entry "Git credential injection via `http.extraHeader`" from the prior session is now partly inaccurate (claims `-c` is never persisted) — a correction was appended as a new entry rather than editing the original.

---

## Session — 2026-07-21 19:57

**Worked on:** QA of the frontend GitHub login flow (built in the prior session, still uncommitted). No new code written.
**Done:**
- Read through the uncommitted diff (CORS middleware in `main.py`, `web/src/lib/api.ts`, TanStack Query wiring in `App.tsx`/`main.tsx`) to get current with what had already been built.
- Manually tested login/logout end to end via the browser (Chrome-in-Claude declined by user, so tested by hand): normal browser session skipped straight past GitHub's login/consent screen on re-login after logout; confirmed in an incognito window that this was GitHub reusing an existing `github.com` session + prior app consent, not a bug — the actual login/consent screen showed up as expected with no session state.
- Explained why Noetra's logout doesn't (and shouldn't) log the user out of github.com — local session termination vs. federated/RP-initiated logout, and that GitHub's OAuth (non-OIDC) doesn't expose a logout endpoint at all.
**In progress:** Nothing code-wise. Uncommitted diff from the prior session (frontend login flow) is confirmed working and ready to commit.
**Key decisions:** None — QA/explanation only. Confirmed the existing logout behavior (clear local session cookie only) is correct as-is; no change needed.
**Next step:** Commit the frontend login flow diff, then decide between Milestone 4 (Parse + extract) or a minimal repo-list UI.
**Watch out for:** Nothing new. Chrome browser automation was declined by the user this session — future UI testing needs to be either manual or re-offered via `/chrome`.

---

## Session — 2026-07-22 02:00

**Worked on:** Built the file tree browser (`docs/FEATURES.md` #3) end-to-end — backend `File` model/endpoints, frontend tree + Monaco viewer — then spent most of the session hardening `clone_repository` against real import failures found by testing against large real-world repos.
**Done:**
- Backend: `File` model + migration; `clone_repository` extended to walk `git ls-files` and persist `path`/`content`/`is_binary`/`content_hash` per file; `GET /repos/{id}/files` + `GET /repos/{id}/files/{file_id}`; `_get_owned_repository` ownership check (404, not 403 — avoids leaking which repo IDs exist). Added `GET /repos` (list), `POST /repos/{id}/retry`, `DELETE /repos/{id}` so failed imports have a real recovery path instead of being silent dead ends.
- Frontend: `RepoList` (import/list/retry/remove), `FileTree` (recursive, collapsible), `FileViewer` (read-only Monaco), `RepoExplorer` shell, wired into `App.tsx`. Installed `@monaco-editor/react`. Reworked the whole app to an always-dark theme (dropped light mode) with a sidebar restyled after a GitHub-dark reference screenshot (blue folder icons, left-accent bar on the selected file).
- **Import bugs found and fixed, all via testing against real large repos (`fbsamples/f8app`, the Linux kernel, TensorFlow):**
  1. NUL bytes in file content crashed the bulk `INSERT` (Postgres rejects `\x00` in text columns). UTF-8 decoding alone doesn't catch this — NUL is valid UTF-8. Fixed by checking for `\x00` in raw bytes before attempting to decode.
  2. That crash left the repo stuck at `status=cloning` forever with no visible error — `clone_repository` had no top-level exception handler. Fixed: the whole task is now wrapped in `try/except`, setting `status=failed` + `error_message` on any unexpected exception instead of hanging silently.
  3. Symlinked paths pointing at directories (Linux kernel's `scripts/dtc/include-prefixes/*`) raised `IsADirectoryError` when read as a normal file. Fixed by reading the symlink's target itself (`Path.readlink()`) instead of following it — matches what git actually treats that path's tracked content as.
  4. No timeout on `git clone`/`git ls-files` — a hang would occupy a worker slot forever. Added a 300s timeout; `subprocess.TimeoutExpired` is caught **separately** from the generic handler because its default string form embeds the full command list — including the Basic-auth header with the token in it — which must never land in `repo.error_message`.
  5. No file-size guard — one huge file would load entirely into memory and bloat a single DB row. Added a 1MB per-file cap; oversized files are hashed in chunks (never loaded whole) and marked non-previewable instead of storing content.
**In progress:** File tree browser is functionally complete and tested against several real repos (Hello-World, f8app, Linux kernel, TensorFlow, react-devtools). The always-dark theme/sidebar redesign is code-complete but not yet reviewed live in the browser — dev server was stopped before a final visual check.
**Key decisions:**
- File content lives in Postgres, not read from the shared Docker volume at request time — `api`/`worker` sharing a filesystem is a local-dev convenience that won't hold on a real cloud deploy, and DB storage avoids a path-traversal attack surface entirely.
- Failed repos are never auto-deleted — kept with `error_message` visible, plus explicit Retry/Remove actions, matching `docs/WORKFLOW.md`'s "surface a retry action" spec.
- Always-dark theme, no light mode — one deliberate palette instead of maintaining a light/dark split.
- No total-repo-size cap added (only per-file) — deferred since nothing has actually broken on total size yet (TensorFlow imported fine); not fixing a problem that hasn't occurred.
**Next step:** Live-review the always-dark theme + modernized sidebar in the browser. After that, likely Milestone 4 (Parse + extract via Tree-sitter) — the file tree browser was built ahead of the numbered milestone order, per `docs/FEATURES.md` #3's explicit note that it's independent of parsing.
**Watch out for:** No repo-size cap exists yet — a truly enormous monorepo could still take a long time or fill disk; this is a deliberate deferral, not an oversight. `is_binary=True` is now overloaded to mean "not previewable" for three different reasons (actually binary, a symlink, or oversized) — fine for the current single "can't preview" placeholder, but worth remembering if a future feature needs to tell these cases apart.

---

## Session — 2026-07-22 13:29

**Worked on:** Q&A on Redis's role in the project, then closed a real concurrency bug found during that discussion: duplicate/racing indexing jobs for the same repo.
**Done:**
- Walked Redis's actual usage (Celery broker + mostly-unused result backend) and confirmed auth is Starlette signed-cookie sessions, not JWT and not Redis-backed.
- Identified two races in `api/repos.py`: `retry_repository` had no protection against two rapid retries both re-enqueuing `clone_repository` for the same row (real bug — `shutil.rmtree` + re-clone racing on disk); `create_repository`'s duplicate-import check was a TOCTOU race, but turned out to already be closed by an existing DB unique constraint (`uq_repositories_user_id_github_url` in `models.py`) — just needed the `IntegrityError` caught and turned into a 409 instead of a 500.
- Added `core/redis_client.py`: `acquire_index_lock`/`release_index_lock`, a per-repo mutex via `SET key val NX EX 600` (`INDEX_LOCK_TTL_SECONDS`).
- Wired the lock into `create_repository` and `retry_repository` (acquire before enqueueing, 409 if already held) and into `clone_repository`'s `finally` block (always released when the job ends, success or failure).
- Verified interactively inside the running `api` container: acquire → `True`, second acquire while held → `False`, `TTL` reads back as 600, `release` deletes the key, re-acquire after release → `True`.
- Committed as `3179a0f` — "Lock repo indexing in Redis to prevent duplicate concurrent jobs".
**In progress:** Nothing code-wise; this was a complete, scoped fix.
**Key decisions:**
- Retry's race → Redis lock (transient, per-repo, needs to self-expire). Create's race → existing Postgres unique constraint, not a Redis lock (permanent uniqueness rule belongs in the DB, not re-implemented as a side-channel lock).
- Lock TTL set to 600s, well above the 300s `CLONE_TIMEOUT_SECONDS` cap — TTL is a crash-only safety net (worker killed mid-task), not meant to ever fire under normal operation, since the `finally` block releases explicitly first.
- No fencing token / compare-and-delete on lock release — deferred, since it only matters once a task can run longer than the TTL, which isn't reachable yet given the current 300s clone timeout.
**Next step:** Milestone 4 (Parse + extract via Tree-sitter) is the next unstarted milestone. Also flagged but not started: GitHub API rate limiting via Redis (relevant once repo-listing/browsing against the GitHub API is added), and Redis pub/sub for live clone/index progress (nice-to-have UX, not required by current scope).
**Watch out for:** The lock's TTL clock starts at *acquire* time (enqueue), not when the worker actually picks up the task — if the queue were ever backed up close to 600s, a lock could expire before its job even starts, letting a duplicate slip through. Not a real risk yet (single worker, near-instant pickup), but worth remembering if concurrency/worker count changes later.

---

## Session — 2026-07-22 19:15

**Worked on:** No code. Design review of milestones 4–6 (working backwards from the end result: "user asks a question → agent answers with clickable `file:line` citations"), then rewrote the planning docs to match the conclusions. Also locked in OpenAI as the AI provider.
**Done:**
- Reordered the build order in `CLAUDE.md`: `4` parse + symbol table + lexical index + dependency graph (no AI calls at all) → `5` eval harness + search endpoint/UI over lexical+structural → `6` LangGraph chat agent → `7` chunking + embeddings + semantic + fusion + rerank → `8` metrics/dashboard. Old steps 7–8 ("chat v1 single-shot RAG" then "chat v2 agent") collapsed into one milestone.
- Reordered the *pipeline* too (separate thing from build order): `cloning+lexical → parsing → graphing → chunking → embedding → metrics`. `graphing` moved ahead of `chunking`/`embedding`; `embedding` is now last before metrics.
- `docs/RETRIEVAL.md` gained two new sections — **Build order & measurement** (build-cost vs. cost-to-redo table) and **Evaluation** (~40 pinned questions, `recall@5`/`recall@20`) — plus three missing pieces: contextual prefix on chunks before embedding, a rerank step after RRF, and a latency-budget section.
- `docs/WORKFLOW.md`: progressive unlock documented (file tree + lexical search after `cloning` → symbol lookup after `parsing` → chat at `ready`).
- `docs/DATA_MODEL.md`: `file.content_tsv` generated column added; `chunk` gained `embed_text`; `chunk.embedding` pinned to `vector(1536)`; indexes list became a table with a milestone column + HNSW-vs-IVFFlat note.
- Provider swap to OpenAI across `ARCHITECTURE.md`, `FEATURES.md`, `SETUP.md`, `.env.example`, `CLAUDE.md` (`OPENAI_API_KEY` / `OPENAI_CHAT_MODEL` / `OPENAI_EMBEDDING_MODEL`). `SETUP.md`'s env block also picked up `FRONTEND_URL`, which had drifted out of sync with `.env.example`.
**In progress:** Nothing code-wise. Milestone 4 is still unstarted — the docs it will be built against are now correct.
**Key decisions:**
- **Retrievers ship in cost order, cheap first.** Lexical is one migration and trivial to throw away; embeddings cost a full re-embed of the corpus whenever chunking strategy changes — and chunking is exactly what changes after first contact with real failing queries. So: build cheap, measure, then buy the expensive one.
- **Eval set before the third retriever.** ~40 questions with known answer locations, scored by `recall@k`. Without it, every embedding knob (chunk size, `k`, threshold) is guesswork and no regression can be attributed to a specific retriever.
- **No single-shot RAG milestone.** Once retrievers are exposed as tools, the agent loop is a small amount of code on top; the single-shot version would be deleted a week later.
- **RAG over CAG.** CAG (whole repo in the prompt, lean on prompt caching) breaks on repo size (~1M tokens ≈ ~100k LOC), costs ~10× more per question, and — the actual killer — can't keep N users × M repos warm inside a 5-min/1-h cache TTL. Kept two ideas from it: stable prompt prefix so automatic caching hits, and a small-repo fast path as a V2 seam.
- **Agentic search over vector-first.** For code, the identifier you want is usually literally in the file; embeddings earn their keep only on conceptual questions where the user's words appear nowhere in the codebase. Real class, but a minority — hence lexical/structural carrying more weight than vector-search-first intuition suggests.
- **OpenAI `text-embedding-3-small`** (1536 dims) as the default; `dimensions` parameter means `-large` can be truncated into the same column later without a migration.
- **Citations come from tool-result metadata, never from the model** — the retriever already knows file + line range; asking the model to report where it found something invites drift.
**Next step:** Milestone 4 — Tree-sitter parse + extract into `code_entity`, plus the `content_tsv` generated column + GIN index and the dependency-edge resolution, all in one migration. No AI calls in that milestone.
**Watch out for:** `backend/core/config.py:20-22` still declares `anthropic_api_key` / `embedding_provider` / `embedding_api_key` — the docs and `.env.example` moved to OpenAI but the code didn't (deliberate: this session was docs-only). All three default to `""` so nothing breaks, but they need renaming to `openai_api_key` / `openai_chat_model` / `openai_embedding_model` before `core/ai` is written. The local `.env` also still has `ANTHROPIC_API_KEY` in it.

---

## Session — 2026-07-24 03:58

**Worked on:** Milestone 4 (Parse + extract) — built and verified end-to-end: Tree-sitter symbol extraction, the lexical index, and the import/dependency graph.
**Done:**
- Added `tree-sitter` + `tree-sitter-python`/`-javascript`/`-typescript` (the latter covers both `.ts` and `.tsx` via two grammars in one package).
- Built `indexer/parser.py` — pure Tree-sitter extractor for functions/classes/methods (including arrow functions bound to `const`/class fields, confirmed via a live grammar probe) plus clean import module strings, across `.py/.js/.jsx/.ts/.tsx`.
- Added `CodeEntity` model + `file.language`/`loc`/`content_tsv` (generated `tsvector` column) + `pg_trgm` extension + GIN indexes on both; migrated (`4e35f936f155`).
- Built `indexer/graph.py` — pure import resolver (absolute Python imports via suffix-matching against known repo paths, since the import root/`src`-layout isn't known ahead of time; relative Python imports via exact dot-counting; JS/TS via relative-path + extension/`index` resolution). Bare/stdlib/npm specifiers correctly drop out (nothing to resolve against).
- Added `DependencyEdge` model + migration (`fb86cf1b15fb`) — had to hand-strip two spurious `drop_index` lines autogenerate proposed for the hand-written lexical/trigram indexes it couldn't see in the model diff.
- Wired both stages into `worker/tasks.py::clone_repository`: `cloning → parsing → graphing`, persisting `CodeEntity`/`DependencyEdge` rows and file `language`/`loc` along the way. `Repository.status` now correctly parks at the last stage actually completed (`GRAPHING`) instead of falsely jumping to `READY` — closes a gap flagged in the 2026-07-21 session log.
- Verified end-to-end against two real repos: `pypa/sampleproject` (correct entities incl. a class+method, correct `src/`-layout absolute-import resolution) and a synthetic multi-package layout for the trickier relative-import dot-counting cases.
**In progress:** Nothing code-wise — Milestone 4 is functionally complete per `CLAUDE.md`'s build order (symbol table + lexical index + dependency graph, no AI calls).
**Key decisions:**
- Module specifier extraction (`.utils`, `./foo`, `pathlib`) moved into `parser.py` rather than staying deferred to graphing as originally planned — it's still pure syntax (a direct Tree-sitter field read), so only the *resolution* to an actual file belongs in graphing.
- Arrow functions (`const foo = () => {}`) and class-field arrow handlers (`handleClick = () => {}`) are extracted as real entities — confirmed via a live grammar dump that `variable_declarator`/`field_definition` expose a `value` field pointing at `arrow_function`/`function_expression`, rather than guessing at node names.
- No unique constraint on `dependency_edges` — `resolve_dependencies` already dedupes via a `set`, so a DB constraint would only ever fire on an app bug, not a real scenario.
- Nested closures/helper defs inside a function body are deliberately not extracted as separate entities (kept the symbol table to top-level/class-member shapes).
**Next step:** Milestone 5 — eval harness (~40 pinned questions → `recall@k`) + search endpoint/UI over lexical + structural retrieval. First real user-facing surface for everything built in M4.
**Watch out for:** Postgres enums added via `ALTER TABLE` (not `CREATE TABLE`) don't auto-create their type — hit this once already (`file_language`); if a similar enum column gets added to an existing table later, remember the explicit `.create(op.get_bind(), checkfirst=True)` + `create_type=False` pattern in `4e35f936f155`. Also: alembic autogenerate can't see hand-written raw-SQL indexes (the GIN ones) in its model diff and will propose dropping them on any later table change — always read a generated migration's `upgrade()`/`downgrade()` before applying, don't just autogenerate-and-run.

---

## Session — 2026-07-24 04:41

**Worked on:** Manual QA of Milestone 4 against real imports (own repo + `tensorflow/tensorflow`), which surfaced and fixed one frontend regression and two real backend gaps.
**Done:**
- Frontend `RepoList.tsx` had `OPENABLE_STATUSES`/the Remove button both hardcoded to `status === 'ready'` from before M4 existed — since the pipeline now legitimately parks at `GRAPHING` (chunking/embedding/metrics don't exist yet), repos could no longer be opened or removed at all. Fixed both to key off the real post-clone stage set; also caught and corrected a first-pass mistake that included `cloning` itself as openable (file rows aren't committed until `cloning` *finishes*, so a large repo briefly showed an empty tree).
- Confirmed via `git status`/DB query that `Repository` deletion cascades cleanly through `files` → `code_entities`/`dependency_edges` via Postgres `ON DELETE CASCADE` — no app code does the cascading.
- Found a real perf bug testing against `tensorflow/tensorflow` (36k files): the graphing stage took ~10 minutes total, ~8 of which was import resolution alone. Root cause: `resolve_python_import`'s absolute-import path did a full linear scan over every known file path for every single absolute import (O(imports × files)). Fixed with `build_suffix_index` — a reverse index (every path suffix → matching paths) built once per repo, turning each lookup into O(1). Verified ~10,000x speedup via a synthetic stress test at the same scale, with identical resolution output on all existing correctness tests.
- Found that `delete_repository` never cleaned up the on-disk clone directory (only the DB rows cascade) — an orphan that grows forever and eventually risks filling the shared disk. Fixed by adding a `worker.tasks.delete_repository_clone` Celery task, enqueued by the API after the DB delete (kept off the request thread, consistent with "anything touching a repo is a Celery task" from `CLAUDE.md`). Verified end-to-end and cleared 17 pre-existing orphaned clone directories left over from this session's testing.
- Committed all of the above across several focused commits.
**In progress:** Nothing code-wise.
**Key decisions:**
- Disk cleanup on delete is itself a background Celery task, not inline in the `DELETE` endpoint — a large repo's `rmtree` could be slow, and `api` should never do slow/repo-touching work inline.
- No repo-size cap or disk-usage alerting added — the immediate leak (never cleaning up at all) is fixed; monitoring total disk usage is a separate, not-yet-needed concern.
**Next step:** Milestone 5 — eval harness (~40 pinned questions → `recall@k`) + search endpoint/UI over lexical + structural retrieval, per `CLAUDE.md`'s build order. Still the next unstarted milestone.
**Watch out for:** All `Repository` rows were deleted during this session's manual testing — the DB is currently empty of repos, and `/data/repos` was wiped clean to match (nothing orphaned left over). Also: `docker compose exec` on Windows/Git Bash mangles absolute Unix paths like `/data/repos` into Windows paths — prefix with `MSYS_NO_PATHCONV=1` when running shell commands against a container that need a literal leading-slash path.

---

## Session — 2026-07-25 04:13

**Worked on:** Milestone 5 (retrieval + eval) — built the hybrid retrieval module, the search endpoint, and the eval-harness seeding; also a design discussion that reshaped M6/M7. Not finished (eval scorer + questions + search UI remain).
**Done:**
- **Docs (committed `4dcdd22`):** folded a **repo map** into M6 (AI-free PageRank-ranked table of contents from `code_entity` + `dependency_edge`, in the prompt prefix, to fix agentic search's blind first-guess on vague queries); made semantic retrieval an explicitly **conditional M7** (built only if the eval proves the cheap stack fails). Touched `CLAUDE.md`, `docs/RETRIEVAL.md`, `docs/FEATURES.md`.
- **`core/retrieval/` module (committed `27070eb`):** `lexical.py` (full-text over `content_tsv` via `websearch_to_tsquery`/`ts_rank`, plus `_best_line` to turn a file match into a line citation), `structural.py` (symbol lookup over `code_entities` — exact + `pg_trgm` `%` fuzzy), `fusion.py` (RRF, `k=60`), `types.py` (`RetrievalHit`), `__init__.py` (`search()` entry point). Verified the pg_trgm `%` operator compiles under psycopg2.
- **`/search` endpoint (in `27070eb`):** `GET /api/v1/repos/{id}/search?q=` in `api/repos.py`, ownership-checked, returns `list[RetrievalHit]` (FastAPI serializes the Pydantic model natively). Confirmed registered via OpenAPI.
- **Refactor (committed `31fc158`):** extracted `index_repository_files(db, repo, repo_dir)` into new `worker/indexing.py`; `clone_repository` now owns clone/auth/status/lock/error-handling and calls it — so the eval seed indexes via the exact production path. (Minor: timing logs now split `clone` vs new `files` stage.)
- **Eval seeding (committed `608cceb`):** `eval/repos.py` (3 repos pinned to SHAs: noetra `31fc158`, requests `69f8484`, zod `912f0f5`), `eval/seed.py` (clone-at-SHA + `index_repository_files`, deterministic `uuid5` ids, idempotent, `--force`). **Seeded all 3 into the DB** and ran the first live `search()` — it works and finds the right symbols/files.
**In progress:** M5 eval harness — seeding done; **`eval/run.py` (scorer: recall@5/@20 scoreboard) and `eval/questions.yaml` (~40 pinned questions + answer keys) NOT built yet.** Search UI also still to build.
**Key decisions:**
- Repo map → **M6**, not M5 (it's agent-orientation, not a fusion retriever). Embeddings → **conditional M7** (measure-then-buy; eval decides).
- Eval seed **borrows the logged-in user's stored OAuth token** (which has `repo` scope, `core/github.py` SCOPES) to clone private noetra — no PAT needed. `EVAL_GITHUB_TOKEN` is an optional CI override.
- Eval lives under **`backend/eval/`** (shares the `/backend` import root; it's a benchmark, not a `tests/` unit suite — a pytest regression gate comes later once a baseline exists).
- Pin eval repos to immutable **commit SHAs** so answer-key line numbers never drift; noetra pinned + indexed as a snapshot since we keep editing `develop`.
**Next step:** Build `eval/run.py` (load `questions.yaml` → `search()` → recall@5/@20, broken down by `kind` (symbol/keyword/conceptual) and repo) and author `questions.yaml` (~40 Qs; grep noetra directly, read requests/zod at their SHA). Then the search UI.
**Watch out for:**
- **Fusion doesn't merge overlapping lexical+structural hits at the same location** — it keys on exact `(file_id, start_line, end_line)`, so a lexical line-hit `(15,15)` and structural entity `(16,34)` show as two rows. Deliberately deferred (interval-overlap merge) until the eval proves it costs recall — visibly happening in the smoke test.
- **zod resolved only 3 dependency edges** (monorepo with `@zod/*` alias/bare imports; our JS/TS resolver only follows relative `./ ../`). Expected, not a bug — but zod "what imports what" questions won't work; use symbol/keyword Qs there.
- Seed must run in the **worker** container (has git + DB + `env_file: .env`), e.g. `docker compose exec worker python -m eval.seed`. `RetrievalHit` is Pydantic (mutable) — fusion mutates `sources` in place.
- `_best_line` matches literal substrings while tsvector matches *stemmed* words — a stem-only match falls back to line 1. Minor; eval will show if it bites.

---

## Session — 2026-07-26 00:39

**Worked on:** Milestone 5 — finished the eval harness, then used it as intended: measured a baseline, found real retrieval bugs, fixed them, and re-measured each time. Also built AST chunking (pulled forward from M7). Search UI still not built, so M5 is *not* complete.
**Done:**
- **`eval/run.py` + `eval/questions.yaml` (`a402485`):** 42 questions across noetra/requests/zod, every answer range read from the pinned SHA. Scorer records the *rank* of the first correct hit, so any `recall@k` falls out of one pass. Reports line-level (right file AND overlapping lines) next to file-level, so "found the file, cited the wrong line" is visible as its own failure mode. Validates every answer path against the seeded DB first — a typo'd path can't silently deflate the score. Added `pyyaml`.
- **First baseline: line-level `recall@5` 0.60, `recall@20` 0.62.** symbol 1.00, keyword 0.79/0.86, **conceptual 0.00/0.00**.
- **Query relaxation (`8a8400e`):** `websearch_to_tsquery` joins bare terms with **AND**, so a natural-language question demanded every term in one document and matched almost nothing. Now: strict pass first, then backfill unused slots from an OR'd rewrite. Strict hits keep their positions, so it cannot regress precision — keyword/symbol came back byte-identical, proving it. → `@20` 0.62→0.69.
- **Citation fix (`dbd4a56`):** `_best_line` matched *literal substrings* while the index matched *stems*, and returned **line 1** when nothing matched. Now pulls Postgres' own lexemes via `tsvector_to_array(to_tsvector(...))` and matches by stem prefix, scoring each line with its neighbours so a lone term in an import loses to a dense block. → `@20` 0.69→0.79, keyword 0.86→0.93.
- **AST chunking (`12c3c48`):** new `indexer/chunker.py` (pure, no DB), `Chunk` model, migration `7c1a4b9e2d33`, and a `chunking` pipeline stage. One chunk per **leaf entity** plus **gap chunks** covering everything else, so no line is unsearchable. `content_tsv` is generated over `embed_text` (the `path › class › signature` prefix + body), not raw content.
- **Chunk-level retrieval (`8fad4dc`):** lexical now searches `chunks` instead of files, so every hit carries a real line range and `_best_line` no longer decides citations at all.
  - On its own this **regressed** `@20` from 0.79 to 0.74. A diagnostic (count distinct files in the top 20) found two separate causes.
  - **Cause 1 — crowding.** 20 slots used to mean 20 distinct files; with chunks it meant 20 chunks from 10 files. `docs/CONCEPTS.md` alone took 8 of 20 slots. Fixed with a per-file cap (`_MAX_CHUNKS_PER_FILE = 2`) applied in SQL via `row_number() OVER (PARTITION BY file_id ...)`, since `LIMIT` would otherwise discard the other files first.
  - **Cause 2 — prose outranks code.** An `english` tsvector ranks writing *about* code above code. noetra's entire top-20 was `docs/*.md`; requests returned `HISTORY.md` and `LICENSE`. Fixed with a `ts_rank` penalty (`_NON_SOURCE_RANK_FACTOR = 0.3`) where `file.language IS NULL`.
  - Both fixes together: `@5` 0.76, `@20` 0.81 — the regression recovered and then some.
- **Docs guard-rail (`532d703`):** the prose penalty was measured against an answer set that is 100% source files, so it could only ever look good — cranking it to 0.001 would have "scored better" while making docs unreachable.
  - Added a 4th question kind, `docs`, with 4 questions whose answers genuinely live in prose. If the penalty is ever tuned too hard, that row collapses and says so.
  - Result `@5 0.75 / @20 1.00` — 0.3 demotes docs without burying them.
  - Made it a *separate kind* rather than more `conceptual` questions, so the original three buckets keep their denominators and stay comparable to every earlier run.
- **Net on the original 42: `recall@5` 0.60 → 0.76, `recall@20` 0.62 → 0.81, misses 16 → 8.** Zero AI, zero new services, one new dependency.
**In progress:** M5's **search UI** is the only remaining deliverable and is not started. All three eval repos are re-seeded with chunks; DB head is `7c1a4b9e2d33`; working tree clean.
**Key decisions:**
- **Chunking before embeddings** — of the remaining misses, only **2 of 46** fail to surface the correct file at all; the rest are localization inside a file already found. Embeddings don't fix localization, chunking does — and chunking is a prerequisite for embeddings anyway, so it's step one either way, done in an order that makes step two optional.
- **Leaf-entity chunks + gap chunks**, not one chunk per entity — avoids storing a class's source once per method, while gaps guarantee full coverage of docs/config files.
- **Index the `tsvector` over `embed_text`, not `content`** — folds file path and enclosing class into the lexical index for free (contextual retrieval applied to the lexical leg).
- **Migration hand-written, then verified by autogenerating a throwaway** — autogenerate proposes dropping the three raw-SQL GIN indexes it can't see. Throwaway's `upgrade()` contained *only* those three drops, proving model and migration agree; deleted after.
- **`docs` as a 4th kind, not extra `conceptual` questions** — adds a guard-rail without shifting any existing bucket's denominator.
**What we got wrong first (worth remembering — the docs above read like a clean run, and it wasn't):**
- **Claimed the 0.00 conceptual score was purely a query-compilation bug.** The AND-joining was real and was starving the retriever, but the diagnostic showed the target file (`core/security.py`) contains none of "credentials / protected / written / database" — it says "encrypt", "token", "at rest", "DB". So OR-ing alone would not have found it either. Lesson: the mechanism was right, the conclusion was half wrong — verify a hypothesis before acting on it, even a well-reasoned one.
- **Justified chunking with the wrong argument.** Sold it on recall ("6 of 9 misses are right-file-wrong-line"). Challenged on the grounds that the end product is an agent that reformulates queries anyway. The stronger justification came out of that: chunking is about the **payload** (a file-level hit forces a second `read_file` that drags ~1,200 lines into context to answer a question about 25) and about the **citation being true at all** (file-level citations are guessed, and "exact file + line citations" is the product's core promise). Also, the bucket chunking actually improved was **keyword** (0.79 → 0.93) — which is precisely the query shape the agent *will* send.
- **Predicted the diversity regression as a "maybe" and shipped anyway.** It landed as a real `@20` regression, and the prose problem underneath it wasn't spotted at all until the diagnostic ran. Chunking is a net win now, but it took two follow-up fixes to get there.

**Next step:** Build the **search UI** over `GET /api/v1/repos/{id}/search` to close M5. Then M6 (repo map + LangGraph agent).
**Watch out for:**
- **`CLAUDE.md`, `DATA_MODEL.md`, `LEARNING_LOG.md` are now behind the code.** `CLAUDE.md`'s build order still lists chunking under M7 though it shipped in M5; `DATA_MODEL.md`'s chunk table lists an `embedding vector(1536)` column the model deliberately omits (M7 + pgvector); `LEARNING_LOG.md` has no entry for any of this (M5 incomplete, so none was written).
- **zod is the outlier and has not responded to any change** — 0.62 line-level vs 0.85 file-level, the widest gap of the three. Theory (unverified): `export const X = core.$constructor(...)` isn't extracted as an entity, so those regions land in 80-line gap chunks instead of function-aligned ones, and retrieval picks the wrong region inside the right file.
- **`recall@20` is a soft bar at these repo sizes** (20 candidates ≈ 15–25% of a 60–130-file repo). Quote `@5`. Also, `overall` is now over n=46 and not directly comparable to the n=42 runs — track the by-kind rows.
- **The eval feeds raw English to `search()`; the M6 agent never will** — it will reformulate first. So the conceptual bucket is realistic for the search UI and pessimistic for the agent path. When M6 lands, extend `run.py` to score agent-mediated retrieval alongside raw `search()`.
- `_MAX_CHUNKS_PER_FILE = 2` and `_NON_SOURCE_RANK_FACTOR = 0.3` are untuned first guesses that happened to work; both are single-constant knobs the eval can settle.
- Re-seeding after any chunker change is mandatory (`docker compose exec worker python -m eval.seed --force`) — chunks are built at index time, not query time.

---

## Session — 2026-07-26 16:59

**Worked on:** Q&A on how lexical/structural retrieval and chunking actually work, which led to measuring and then removing structural retrieval from the fusion.
**Done:**
- Walked lexical search (query relaxation, per-file cap, non-source penalty), structural search (exact + trigram-fuzzy `code_entity` lookup), RRF fusion, and AST chunking with concrete examples from the real code.
- Clarified a real gap the user's mental model assumed existed: there is **no call-graph** anywhere in the codebase — `dependency_edge` is file-level import resolution only, not "who calls this function." No inheritance data either (`CodeEntity` has no base-class field).
- Ran the actual ablation the eval harness exists to enable: brought up `docker compose`, seeded the 3 eval repos (already seeded from a prior session), ran `eval.run` with structural search stubbed out of `core/retrieval/search()` vs. left in. **Hybrid: recall@5 0.76 / @20 0.83. Lexical-only: recall@5 0.72 / @20 0.78.** The one symbol question that flips is `$ZodRegistry` — a `$`-prefixed identifier the Postgres text-search tokenizer mangles; trigram similarity doesn't care about `$`, so structural still found it.
- Discussed (and rejected, unmeasured) a call-graph-seeded RRF pattern the user found elsewhere — seeding a "structural" retriever's graph expansion from the top semantic/lexical hit. Rejected because: (a) it needs call-graph extraction Noetra doesn't have, (b) it targets the wrong failure bucket (the actual weak spot is `conceptual`, which is M7's job, not graph traversal), (c) it couples retrievers together (seeding from a wrong top hit reinforces the mistake) where the current independent-retriever-then-RRF design deliberately keeps errors uncorrelated.
- **Decision: strip structural retrieval entirely**, given a +0.04 recall@5 win didn't justify the complexity for this project's scale. Deleted `core/retrieval/structural.py`; simplified `search()` to lexical-only; cleaned `RetrievalHit`/`RetrieverSource`/`fusion.py` of now-dead `entity_name`/`entity_kind`/`STRUCTURAL` fields; updated frontend (`search.ts`, `SearchPanel.tsx`) to match; wrote and applied migration `7a96b345a5d0` dropping the now-unused `ix_code_entities_name_trgm` pg_trgm index (verified gone via `pg_indexes`); updated `CLAUDE.md`, `docs/RETRIEVAL.md` (added a decision record + a "Deferred: call-graph extraction" section), `docs/FEATURES.md`, `docs/DATA_MODEL.md`, `docs/CONCEPTS.md` to match. Re-ran the eval post-removal to confirm it reproduces the lexical-only ablation numbers exactly. Committed as `24e3e88`.
**In progress:** Nothing code-wise — this was a complete, scoped removal, verified end-to-end. M5's search UI is still the open item from the prior session (not touched this session).
**Key decisions:**
- **Cut structural retrieval, not just documented the tradeoff** — the project's own rule ("no retriever joins the fusion without a `recall@k` movement that justifies it") cuts both ways; a measured, marginal, single-edge-case win wasn't enough to keep it.
- **Removed the planned `find_symbol` agent tool from `docs/RETRIEVAL.md`'s M6 tool list** — it was structural's agent-facing form; `code_search` (lexical) already covers the same ground per the eval.
- **Call-graph extraction stays deferred, not built** — no measured need yet; added as a concrete, evidence-gated V2 item in `CLAUDE.md` rather than left as vague scope creep risk.
- **Dropped the DB index in this session rather than leaving it** — user explicitly asked; a new Alembic migration was the correct mechanism (code removal alone doesn't touch schema), applied to the local dev DB and confirmed via `pg_indexes`.
**Next step:** Build the search UI (still the open M5 item — unaffected by this session's changes beyond `RetrievalHit` no longer carrying `entity_name`/`entity_kind`). Then M6 (repo map + LangGraph agent, now `code_search`/`read_file`/`list_dependencies` — three tools, not four).
**Watch out for:**
- **`docker compose` services were left running** from this session's ablation work (`db`, `redis`, `api`, `worker`) — not stopped, since a future session will likely want them again.
- **`eval/questions.yaml`'s pinned `noetra` snapshot (SHA `31fc158`) still contains `structural.py`** — one keyword question's answer path points at it. This is correct and expected: the eval repo is a frozen historical snapshot, not `develop`, so it stays valid regardless of what `develop` deletes later.
- **`pg_trgm` the Postgres extension itself was left enabled** — only the index that used it was dropped. Removing the extension too is a separate, not-yet-necessary call.

---

## Session — 2026-07-26 18:12

**Worked on:** No code. Full architecture pivot: retrieval becomes **agentic RAG** (three
legs — lexical, semantic, graph — driven by an agent that picks its own strategy per query),
and the AI provider switches from OpenAI to **Google Gemini** (free tier). Realigned every
doc to match, then split `CLAUDE.md` down to size.
**Done:**
- Explored current code state to ground the pivot in what actually exists (lexical-only
  retrieval, no `core/ai`, `core/config.py` still has unused `anthropic_api_key`/
  `embedding_provider`/`embedding_api_key` fields, no chat/agent code anywhere).
- Verified real Gemini/pgvector/LangGraph facts via web search rather than assuming:
  `gemini-embedding-001` defaults to 3072 dims (Matryoshka-truncatable), only pre-normalizes
  at 3072, caps input at 2048 tokens, and needs `task_type=RETRIEVAL_DOCUMENT` vs.
  `CODE_RETRIEVAL_QUERY`; pgvector's HNSW index caps the `vector` type at 2000 dims (hence
  1536, not 3072); free-tier `gemini-2.5-flash` is roughly 10 RPM / 250 RPD.
- Wrote a plan (in plan mode) with the user resolving four open design questions: embedding
  dims (1536, `vector`), the graph leg's wiring (both a seeded third RRF leg **and** direct
  agent tools — the user's own design), call-graph extraction (build it, name-based
  resolution), and the agent loop (hand-rolled `StateGraph`, user explicitly wanted to write
  and understand the whole graph rather than use `create_react_agent`).
- Rewrote `docs/RETRIEVAL.md` (agentic RAG framing, all three legs, the seeded-expansion
  design + its RRF-key-alignment trap + its honest coupling cost, Gemini specifics, why no
  reranker in V1), `CLAUDE.md` (stack, scope, deferred list), `docs/DATA_MODEL.md`
  (`chunk.embedding` now real/nullable, new `reference_edge` table), `docs/ARCHITECTURE.md`,
  `docs/WORKFLOW.md` (pipeline gains a real `embedding` stage), `docs/FEATURES.md` (chat tool
  list, `find_symbol` removed), `docs/SETUP.md` + `.env.example` (`GEMINI_*` vars), and
  `docs/CONCEPTS.md` (corrected two OpenAI-specific prompt-caching claims to Gemini's
  implicit-caching behavior).
- Added an addendum to `docs/LEARNING_LOG.md`'s existing M5 entry — it had no record of the
  structural-retrieval ablation/removal that happened in the prior session.
- **Split `CLAUDE.md` down from 206 to 115 lines** (user flagged it was over the ~200-line
  guideline): extracted the build order into new `docs/BUILD_ORDER.md` and the stack into
  new `docs/STACK.md`, leaving only enforceable rules inline in `CLAUDE.md` (e.g. "the AI SDK
  is imported only in `core/ai`") rather than descriptive detail. Also trimmed
  `docs/RETRIEVAL.md` from 336 to 279 lines by cutting restated prose, keeping every decision
  and its reasoning intact.
- Committed as `f271b44` — "Switch retrieval architecture to agentic RAG on Gemini and split
  stack and build order into their own docs".
**In progress:** Nothing code-wise — this was a complete, scoped docs-only pivot. **No code
has been written against the new architecture yet.**
**Key decisions:**
- **Semantic retrieval is no longer conditional.** It was gated on "only if lexical fails"
  because embeddings were expensive to build and redo; at $0 on Gemini's free tier that
  argument weakens, and the product decision (agentic RAG needs a semantic leg) is made. The
  eval stays a scoreboard, just not a gate.
- **The graph leg is fused (seeded from lexical+semantic top hits) AND exposed as direct agent
  tools** — the user's explicit design. Acknowledged cost: seeding couples the retrievers (a
  wrong seed gets reinforced, not cancelled out), so M7 ends with the same in/out ablation
  that cut structural retrieval in M5.
- **Hand-rolled `StateGraph`, not `create_react_agent`** — user confirmed they want to write
  and control the whole loop (state, `call_model`, `ToolNode`, `should_continue`) rather than
  use the prebuilt, specifically to understand and be able to explain it.
- **No reranker in V1** — no free Gemini reranker exists, and an LLM-as-reranker would spend
  the same ~10 RPM chat quota the agent loop needs.
**Next step:** M6 — start with `core/config.py:20-22` (rename the stale
`anthropic_api_key`/`embedding_provider`/`embedding_api_key` fields to `gemini_*`), then build
`core/ai/embeddings.py` + `core/ai/chat.py`, the `chunk.embedding` migration, the `embedding`
pipeline stage, and `semantic_search()` fused via the already-written (currently unused)
`fusion.py`. Full milestone detail is in `docs/BUILD_ORDER.md`.
**Watch out for:**
- **The baseline number changed.** Docs used to quote `recall@5` 0.76 (the pre-structural-
  removal hybrid figure). The real current lexical-only baseline M6 must beat is **line-level
  `recall@5` 0.72 / `recall@20` 0.78** — this is now stated explicitly in `CLAUDE.md`/
  `docs/BUILD_ORDER.md` with a warning not to quote the old number.
- **`fusion.py` and the `SEMANTIC`/`RetrieverSource` enum slot already exist**, written during
  M5's structural-retrieval detour and currently unused — M6 wires into them rather than
  writing new fusion code.
- **`chunk.entity_id` (already a nullable FK) is load-bearing for M7** — it's what resolves a
  ranked chunk back to a symbol the graph can seed from, and what maps an expanded entity back
  to a chunk-aligned `RetrievalHit` so RRF's `(file_id, start_line, end_line)` dedup key still
  works. Missing that mapping would make the graph leg silently degenerate into concatenation
  that still looks like it's fusing.
- Docker services' running state from the prior session was not touched or verified this
  session (no code/containers were run — this was a docs-only session).

---

## Session — 2026-07-28 22:23

**Worked on:** Milestone 6 (Gemini provider layer + semantic retrieval leg), per `docs/PLAN.md`. Code complete and committed; the milestone's own "measured recall delta" deliverable is not — blocked mid-run by the free tier's daily embedding quota.
**Done:**
- Pinned the baseline before touching anything: `eval.run` reproduced the documented **0.72/0.78** exactly.
- `core/config.py`: removed dead `anthropic_api_key`/`embedding_provider`/`embedding_api_key`; added `gemini_*` settings plus (user-requested, ahead of M8) a `default_chat_provider` + per-provider OpenAI/Anthropic keys.
- New `core/ai/` package: `embeddings.py` (`embed_documents`/`embed_query` — cached `genai.Client` with retry, per-text truncation at a deliberately conservative 2.5 chars/token for code, client-side L2 normalization, token-budget batch packing) and `chat.py` (`get_chat_model(provider)` — a factory returning a ready LangChain chat model for gemini/openai/anthropic, for testing the M8 agent later).
- Migration `b3f4c9a1d2e6`: `CREATE EXTENSION vector` + `chunks.embedding vector(1536)`, nullable, **no ANN index** (deliberate reversal of the old plan — see Decision 3 in `docs/PLAN.md`). Applied; verified `pgvector 0.8.5` active.
- `worker/embedding.py`: `embed_repository()` — resumable (`WHERE embedding IS NULL`), paced (sleep between pages), timed. Wired into `clone_repository`; embedding failures are caught and leave `status=EMBEDDING`, never `FAILED` (closes a real data-loss path — `retry_repository` only bulk-deletes `File` rows on `FAILED`).
- `core/retrieval/semantic.py` + fusion wiring in `__init__.py`: `search()` now fuses lexical + semantic via RRF, gated on data (has-embedded-chunks) not status, degrades gracefully if the semantic call fails, re-applies the per-file cap after fusion.
- `eval/run.py --legs` and `eval/seed.py`'s embed step (resumable, `--no-embed`, non-fatal per-repo — this last one a real bug found and fixed mid-session: the first `--force` run crashed the whole seed on repo 1 of 3 over one repo's quota failure).
- Reconciled 9 docs (`DATA_MODEL`, `RETRIEVAL`, `STACK`, `WORKFLOW`, `SETUP`, `BUILD_ORDER`, `ARCHITECTURE`, `FEATURES`, `CONCEPTS`) with what actually got built, including a corrected HNSW section in `CONCEPTS.md` (appended, not rewritten, per that file's existing correction pattern).
- All of the above committed in 10 small, working increments.
**In progress:** The actual recall measurement. DB state right now: `noetra` fully embedded (248/248), `requests` partial (600/1040), `zod` untouched (0/3563). A preliminary run showed the `conceptual` bucket moving +0.07 (met the stated bar) but is **not trustworthy** — confirmed live that `search()`'s silent semantic-failure fallback was firing during it, so some unknown fraction of the 46 questions silently lost their semantic leg to quota exhaustion instead of genuinely testing it.
**Key decisions:**
- No ANN index in V1 (exact cosine scan) — the per-file cap already forces a full sort, and a plain HNSW/IVFFlat index would silently under-return once `WHERE repository_id =` filters after the fact.
- Embedding failure is non-fatal by design — a quota blip isn't the repo's fault and shouldn't brick an otherwise fully-searchable repo.
- Chat provider factory built now even though M8 doesn't exist yet — explicit user request, not built ahead of need for its own sake.
- Truncation/batching use 2.5 chars/token, not Google's commonly-cited ~4 — that figure is measured on English prose; code is punctuation-dense and tokenizes denser. Verified via Google's own docs rather than taking either side's assertion at face value.
- Retry attempts bumped 5→9 after a live 429 proved 5 attempts' backoff (~31s) fell short of the server's actual 42s suggested delay.
**Next step:** Once the free tier's daily embedding quota resets (or billing is enabled): re-run `eval.seed --force` (resumes cleanly from current partial state), then `eval.run --legs lexical` (must still equal 0.72/0.78 exactly) and `--legs lexical,semantic` for the real fused number. Worth adding fallback-failure logging to `search()` first, so that run can report how many questions actually exercised semantic vs. silently fell back. Then Step 8 (routing) and the final verification pass — both deliberately still unstarted, since the plan makes them depend on a real measurement.
**Watch out for:**
- **Free tier has two separate quotas** — 100 requests/minute *and* **1,000/day** — discovered by hitting both live. The daily one doesn't recover on any short wait, and Google's `retryDelay` hint is misleadingly short either way; only the error's `quotaId` field tells them apart.
- **`search()`'s semantic-leg failure is silent by design** (degrades to lexical, no error, no log). Correct for production, but means no eval number from a quota-exhausted window can be trusted — always sanity-check with one direct `embed_query()` call before trusting a semantic/fused eval run.
- Docker images were rebuilt this session for the new deps (`google-genai`, `pgvector`, `langchain-*`) — a plain restart won't pick up any *future* `pyproject.toml` change; needs `docker compose build api worker` again.
- `.env` now has a real `GEMINI_API_KEY` filled in by the user directly (never passed through chat) — confirmed still gitignored, not committed.

---

## Session — 2026-09-04 22:41

**Worked on:** Closing Milestone 6 — switched the AI provider from Gemini (free tier) to paid OpenAI, stripped every piece of rate-limit machinery, measured the semantic leg for real, fixed fusion twice, and restructured `CONCEPTS.md`/`RETRIEVAL.md`/`DEPLOYMENT.md`. Six commits on `develop` (`1ae53b2`…`ecff5fd`).
**Done:**
- `core/ai/embeddings.py` rewritten for OpenAI `text-embedding-3-small` (1536 native, provider-normalized); `core/ai/chat.py` is `openai` | `anthropic` only; `google-genai`/`langchain-google-genai` removed, `openai` + `tiktoken` added. `EMBEDDING_PROVIDER` + `OPENAI_API_KEY` is the whole switch. Pacing sleep, token-budget batching, retry tuning, `task_type`, client-side L2 normalization — all deleted.
- One guard kept: input truncation by **real token count** (`tiktoken`, 8,000 tokens) — a chars/token guess failed live on `uv.lock` gap chunks (hashes ≈1.5 chars/token) with a 400 from OpenAI.
- `search()`'s silent semantic fallback now `logger.warning`s, so an eval run shows whether semantic actually ran.
- `eval.seed` / `eval.run` gained `--repos` (default `noetra`; `all` or a comma list). All three eval repos re-embedded on OpenAI (noetra 248, requests 1040, zod 3563 chunks; 0 nulls).
- **M6 measured, all 46 questions, line-level recall@5/@20:** lexical 0.72/0.78 (baseline reproduced exactly) · semantic-only 0.85/0.93 · **fused (shipped) 0.85/0.91**. Conceptual @5 0.36 → 0.57 fused (0.64 semantic-only).
- Fusion needed two measured fixes: (1) RRF `k` 60→10 and per-leg fetch `limit` not `2×limit` — on 20-deep lists the paper's `k` let "in both legs at rank 40" beat "rank 2 in one leg", so equal-weight fusion scored 0.72, no better than lexical; (2) **weighted RRF, semantic 2×** — with equal votes lexical out-voted semantic on conceptual questions (0.78); at 2× fused matches semantic's @5 and keeps the docs/keyword hits semantic-only loses; at 3×+ the result is identical to semantic-only. `reciprocal_rank_fusion` now takes `weights`.
- Docs: `CONCEPTS.md` rewritten (1,517 → ~430 lines) as an interview file — Section A problems (A1–A22, incl. the new A20 free-tier story and A22 fusion story), Section B decisions (B1–B19, incl. the stack "why X over Y" entries, B18 paid OpenAI, B19 EC2+Terraform). `RETRIEVAL.md` cut 327 → ~235 lines with the measurement table. `DEPLOYMENT.md` rewritten for one EC2 host running the same Compose stack, provisioned by Terraform (ECS/Fargate deferred with a stated trigger). `STACK`/`ARCHITECTURE`/`SETUP`/`WORKFLOW`/`DATA_MODEL`/`BUILD_ORDER`/`FEATURES`/`README`/`CLAUDE.md` realigned. `/endsession` skill's CONCEPTS step rewritten to the problems/decisions format.
**In progress:** Nothing — M6 is closed with a measured, reproduced number through the real `search()` path.
**Key decisions:**
- Paid OpenAI over free Gemini → the quota machinery was outgrowing the feature and a silent fallback had already produced one untrustworthy number; at ~$0.02/M tokens the cost argument is gone. Deleted rather than parameterised: **no rate-limit code until a 429 actually appears** (user's explicit rule).
- Embeddings single-provider by design → switching models is a full re-embed regardless, so a live switch has no use case; a new provider is one branch in `core/ai`.
- `k=10`, fetch `limit`, semantic 2× → all three set by a sweep on the harness, none from the paper; `k<10` and weight >2 buy nothing (weight ≥3 = semantic-only).
- Eval defaults to noetra for demos; requests/zod kept and runnable via `--repos all`.
- Single EC2 + Compose via Terraform over ECS Fargate → zero dev/prod drift, one bill; split worker/RDS only when queue depth or latency says so.
**Next step:** **M7 — the graph leg.** Call-graph extraction (`reference_edge`, name-based resolution), `expand(seeds, max_hops=2)` over `reference_edge ∪ dependency_edge` seeded from the fused top hits (note: that seed list is now the semantic-2× one), fused as a third RRF list with its own weight to measure, plus `get_callers`/`get_callees`/`list_dependencies` tools. Ends with the same in/out ablation. Baseline to beat: fused 0.85/0.91.
**Watch out for:**
- **`.env` still has dead `GEMINI_*` lines** (ignored, harmless) — delete at leisure. `docs/PLAN.md` (the old Gemini M6 plan) is still untracked; delete it.
- A user-imported `ApoErch/Noetra` repo row has 303 chunks with **NULL embeddings** (Gemini-era import, never embedded). Semantic is data-gated so it just runs lexical-only; re-import or run `embed_repository` on it if it matters.
- **The eval feeds raw English to `search()`**, which favours semantic. The M8 agent will send identifier-shaped queries where lexical and semantic tie (symbol/keyword ≈ 1.0/0.93 either way). Don't read the 2× weight as "lexical barely matters" — it's what keeps docs and keyword@20 at 1.00.
- The remaining 5 misses are all `conceptual`, 3 of them "right file, wrong lines" — a localization problem, the shape M7's graph expansion (or M8's `read_file`) addresses, not more fusion tuning.
- `docker compose restart` does **not** re-read `env_file`; after editing `.env` use `docker compose up -d --force-recreate api worker`. Cost this session ~10 minutes.
- Image rebuild needed after any `pyproject.toml` change (`docker compose build api worker`); done this session for `openai`/`tiktoken`.
- Pre-existing, untouched: 3 ruff findings (`api/auth.py`, init migration) and mypy `celery` stub warnings; `retry_repository` still only accepts `FAILED`, so a repo parked at `EMBEDDING` has no API resume.

---

## Session — 2026-09-04 23:41

**Worked on:** No code. Research + design review of the graph leg (planned M7), triggered by the user challenging "fuse the graph into RRF as a third leg". Ended in a docs-only realignment, committed as `d7582dc`.
**Done:**
- Researched how the field handles graph/structural code retrieval: LARGER, RepoGraph, LocAgent, CodexGraph, Codebase-Memory, GraphRAG-Bench, CodeCompass (papers) and Sourcegraph/Cody, Augment, Greptile, Cursor, Claude Code, Aider (production). **No system fuses graph neighbours into a ranked list.** The graph appears as agent tools, a sidecar attached to a hit (LARGER), or prompt orientation (Aider). RepoGraph: 1-hop best, 2-hop worst. LocAgent: graph tool +4 pts vs keyword search +13. GraphRAG-Bench: graphs lose on simple lookups.
- Reasoning that settled it: RRF combines *independent estimates of query relevance*; hop distance from a seed is a property of the seed, not the query, and a seeded leg can only amplify the other two. Whether a neighbour matters depends on the question — only an agent can judge that per query.
- Realigned `BUILD_ORDER`, `RETRIEVAL` (graph section rewritten with the research digest + a worked agent trace), `DATA_MODEL` (`reference_edge.confidence`), `FEATURES`, `WORKFLOW`, `ARCHITECTURE`, `STACK`, `CLAUDE.md`, `LEARNING_LOG`; `CONCEPTS.md` gained **A23** + **B20**, and **B17** is struck through as superseded.
**In progress:** Nothing. Working tree clean after `d7582dc`.
**Key decisions:**
- **The graph is agent tools, never an RRF leg.** Fusion stays lexical + semantic. Tools: `list_dependencies` (free — `dependency_edge` exists), `get_callees`, `get_callers`; one hop per call; edges carry a resolution confidence (0.9 same file → 0.85 imported file → 0.7 unique name → 0.3 ambiguous), tools filter at ≥0.5.
- **Milestones swapped: M7 = the agent, M8 = the call graph.** The graph's value can only be measured inside the agent loop (tools on vs off), so building it first would ship it unmeasured — the project's own measure-then-buy rule.
- Honest scope: keyword search already finds a name's call sites, so `get_callers` mostly adds the enclosing caller; `get_callees` is the genuinely new capability.
- A LARGER-style `related` sidecar on `/search` hits is deferred, not rejected — trigger is the agent eval still failing multi-hop conceptual questions with the tools present.
**Next step:** Plan and build **M7 — the agent** in its own planning session: tools `code_search` + `read_file` + `list_dependencies`, repo map, hand-rolled LangGraph `StateGraph`, SSE with tool-call status, CRAG-style grade + citation-verification nodes, and the agent-mediated eval in `eval/run.py` (checkpointed per question). That eval is the baseline M8 is measured against.
**Watch out for:**
- Old `docs/SESSION_LOG.md` entries (2026-07-26 18:12, 2026-09-04 22:41) still describe the seeded-third-leg design and "M7 = graph leg" — historical, not current. Trust `BUILD_ORDER.md` / `RETRIEVAL.md` / `CONCEPTS.md` B20.
- `docs/PLAN.md` (old Gemini M6 plan) is still untracked and `.env` still has dead `GEMINI_*` lines — both still pending deletion from the prior session.
- `eval/run.py:232` enumerates the whole `RetrieverSource` enum as the "ran" list when `--legs` is None; adding a `GRAPH` member in M8 without fixing that would mis-report it as a search leg. Use `_ALL_LEGS` there.
- Docker services were not touched this session (no code ran).

---

## Session — 2026-09-05 04:25

**Worked on:** Milestone 7 — the LangGraph chat agent — planned, built, measured, documented, and
committed as 8 commits on `develop` (`f245f6e`…`bb93145`).
**Done:**
- Research pass (Anthropic context-engineering / tool-design posts, Cursor semsearch, Cognition
  SWE-grep, Copilot @workspace, DeepWiki, arXiv 2608.01507 + 2512.12117) → plan diverged from the
  user's draft: **no router node, no LLM grader node**; mechanical guards instead (`CONCEPTS.md` B21).
- `backend/core/agent/`: `tools.py` (`code_search` limit 10, `read_file` ≤200 lines and ≤12k chars,
  `list_dependencies` imports/imported_by; `db`/`repository_id` as `InjectedToolArg`),
  `citations.py` (regex + interval-overlap verifier), `repo_map.py` (hand-rolled PageRank, tiktoken
  budget, cached per repo), `prompts.py`, `state.py`, `graph.py` (`call_model → tools →
  verify_citations`, budget via `tool_choice="none"`, no-progress guard, own tools node),
  `runner.py` (`run_turn` / `stream_turn` — shared by API and eval).
- `api/chat.py`: conversations CRUD + `POST …/conversations/{cid}/messages` as SSE (sync generator,
  fresh `SessionLocal` inside it); `api/deps.py` (`get_owned_repository`); `/search` endpoint gone.
- `chat_conversations` / `chat_messages` tables, migration `c4d5e6f7a8b9` applied locally.
- Frontend: `lib/chat.ts` (fetch-based SSE reader), `ChatPanel.tsx`, `MessageBubble.tsx`
  (react-markdown, `[path:a-b]` → chips → existing Monaco overlay); `SearchPanel` + `lib/search.ts`
  deleted. `pnpm build` clean.
- First `backend/tests/` (19 pure tests) + `[tool.pytest.ini_options]`; `eval/agent.py`
  (retrieved / cited / calls / tokens, JSONL checkpoint per question, `--model --budget
  --no-repo-map --limit --fresh --repos --kinds`); `eval/run.py` leg-reporting fix (`ALL_LEGS`).
- Measured (46 questions): gpt-5.4-mini + map **cited 0.91**, no map 0.87, gpt-4.1-mini 0.59
  (mostly citation *format*); 0 stripped citations across 138 answers. Default model →
  `gpt-5.4-mini`. Docs realigned (RETRIEVAL, BUILD_ORDER, FEATURES, DATA_MODEL, SETUP, STACK,
  ARCHITECTURE, WORKFLOW, README); CONCEPTS A24–A27, B21–B23.
**In progress:** Nothing half-built. The user's manual browser check of the chat UI had not been
reported back when the session ended (dev server was left running on :5173, API recreated on
:8000 with gpt-5.4-mini).
**Key decisions:**
- Simple loop + mechanical guards over router/grader nodes → in a tool loop the model is already
  the grader; the repo-QA study showed added nodes hurt (65 % vs 46 %).
- Own chat tables over a LangGraph checkpointer → never replay old tool results; plain SQL for
  the UI; `END` ends a turn, not the conversation.
- gpt-5.4-mini default → 0.91 vs 0.59 cited, fewer calls, faster, ~1 ¢/question.
- Repo map kept despite only +0.04 → without it the model answered from memory with zero tool
  calls (A25); its token cost is cached-prefix and not yet measured separately.
**Next step:** Either close the M7 eval caveats (widen 3 narrow answer keys + re-score offline;
accept bare `path:a-b` in verifier + UI; cached-token/cents accounting; try an 800-token map) or
start **M8 — call graph as agent tools** (`reference_edge` + confidence, `get_callees`/`get_callers`),
measured with `eval.agent` tools on vs off against the table in `RETRIEVAL.md`.
**Watch out for:**
- Answer keys are one location each and unreviewed — 3 of 4 gpt-5.4-mini "misses" were correct
  answers elsewhere (A26). Treat scores as lower bounds; add locations only after reading them.
- `eval/out/*.jsonl` (gitignored) holds every answer from today's runs — re-scoring `cited` for
  widened keys needs no API calls; re-verifying with a relaxed regex does (hits aren't stored).
- `tokens/q` counts the repo map once per model call; wall-clock says it's cached. Don't cut
  the map on that column alone.
- Chat gates on "repo has chunks", never on `READY` (nothing sets READY until M9).
- `docker compose restart` still doesn't re-read `.env` — use `up -d --force-recreate api`.
- Git Bash heredocs with quoted bodies fail intermittently on this machine — write scripts to
  a file and run them, or use the Write/Edit tools.
- Pre-existing, untouched: 3 ruff findings (`api/auth.py`, init migration), mypy celery stubs +
  `core/github.py:70`.

**Addendum (same session, after the handoff above — commits `f7303e8`…`ca5cec4`):**
- Manual browser test passed; three chat issues found and fixed (`f7303e8`): blank bubbles on
  OpenAI refusals (empty `content`, text in `refusal` → fallback line in `verify`); canned
  off-topic replies (prompt now varies wording, suggests a map-drawn question); inline citation
  links reloading the app (react-markdown drops unknown URL schemes → `urlTransform` keeps
  `noetra-cite:`). Chip row now only shows when no citation is inline.
- **Real bug found:** the user's `ApoErch/Noetra` import stalled at `embedding` — one 200-chunk
  page exceeded OpenAI's 300k-tokens-per-request cap (400). `core/ai/embeddings.py` now
  splits a page by token budget (`_MAX_REQUEST_TOKENS = 250_000`); the stalled repo was
  resumed by hand (157 chunks). Hard size limit, not rate-limit machinery (`be5de44`).
- LangSmith tracing: env-only (`LANGSMITH_*` in `.env.example`/SETUP.md), runs named
  `agent_turn` with `repository_id` metadata (`b0c8dea`). Not yet confirmed in the LangSmith UI.
- Prompt: no process narration, no closing offers, no spaces in citation brackets; verifier
  tolerates and normalises them (`4fca4f3`, tests `ca5cec4`).
- Dev server and background tasks stopped at session end; containers left running.

---

## Session — 2026-09-06 02:08

**Worked on:** Milestone 8 — the call graph as an agent tool. Researched, planned, built,
measured, documented. Six commits on `develop` (`fd038ec`…`a3ad27f`). **M8 is closed.**

**Done:**
- **Research pass first** (the user asked for it before any plan): LocAgent, RepoGraph, ARISE,
  Codebase-Memory, LARGER, SWE-QA, plus Sourcegraph/SCIP, GitHub stack-graphs, Serena/LSP,
  aider. Findings that changed the plan are in `CONCEPTS.md` A28–A31 / B24–B27.
- **Fixed the measurement before building.** The M7 eval was saturated (0.91, ~1 question of
  real headroom, no question asking for a *set* of locations), so an on/off ablation would
  have returned noise. Added `QuestionKind.GRAPH` + 12 enumeration questions (52 answer
  locations, noetra 6 / requests 4 / zod 2) and **coverage scoring** (`rcov`/`ccov`) to
  `eval/agent.py`, since a boolean `cited` scores "2 of 5 call sites" as a win. Graph
  questions are excluded from `eval.run` so the pinned 0.85/0.91 retrieval baseline stays
  comparable. Baseline with the 3 existing tools: **ccov 0.63**.
- **`indexer/calls.py`** (pure, 16 tests): a second Tree-sitter walk that descends *into*
  function bodies (node shapes probed live, not assumed), plus name-based resolution in tiers
  — same file 0.9, one imported file 0.85, unique repo-wide 0.7. **No ambiguous tier**; an
  ambiguous name resolves to nothing and does not fall through. `self.foo()` stops at the
  current file; `x.foo()` is denied the repo-wide tier.
- **`ReferenceEdge` model + migration `d7e8f9a0b1c2`** (verified with a throwaway
  autogenerate — it proposed only the two known raw-SQL GIN drops), wired into
  `worker/indexing.py` inside the existing `GRAPHING` stage, reusing the import edges just
  resolved for the 0.85 tier. No new status enum, no extra pass over files.
- **`find_references(symbol, direction)`** — one tool, not two (`CONCEPTS.md` B25), returning
  chunk-aligned citable `RetrievalHit`s via `chunk.entity_id`. `AgentConfig.tools` +
  `--no-graph-tools` make the ablation expressible; the prompt drops its guidance too, so a
  tools-off run isn't an agent told to use a tool it lacks.
- **Measured (same seed, `--kinds graph --repos all`, n=12):** tool off → on, `ccov`
  **0.63 → 0.84**, `rcov` 0.82 → 0.91, cited 0.75 → 0.92, tool calls **−28%**, tokens
  **−30%**. Per repo `ccov`: noetra 0.72 → 1.00, zod 0.33 → 0.83. No regression on the
  original 46 (cited 0.91 → 0.89 = one question of variance).
- **Edge quality by hand** at the pinned SHAs: `_get_owned_repository` 5/5, `acquire/release
  _index_lock` 2/2 and 1/1, `request` 7/7, `finalizeIssue` 4/4. On `requests`, where `request`
  is defined twice, **all 19 edges resolved to the correct definition** — `self.request()` in
  `sessions.py` never leaked to `api.py`.
- **Real bug found and fixed:** zod had **3 import edges for 1,411 entities**, twice
  misdiagnosed in earlier sessions as "monorepo `@zod/*` aliases". Actual cause: TypeScript
  `moduleResolution: NodeNext` imports the *emitted* path (`"./util.js"` for `util.ts`), which
  `resolve_js_import` never stripped. Fixed → **405 import edges**, call edges 675 → 1,324,
  confidence mix inverted. Had been degrading `list_dependencies` and the repo map for every
  TS repo since M4. Committed separately (`1021efa`).
- **Built, measured, reverted:** reference-weighted repo-map PageRank (aider's design). Graph
  `ccov` 0.84 → 0.72, other 46 cited 0.89 → 0.87. Import edges count *breadth*, call edges
  count *volume*, and orientation needs breadth — a test helper was promoted into the
  token-capped top ten and evicted a real source file (`CONCEPTS.md` A31).
- **Docs realigned:** `CONCEPTS.md` (A28–A31, B24–B27, B20 supersession note, A26 rewritten
  as a deliberate hold), `LEARNING_LOG.md` (Milestone 8), `RETRIEVAL.md`, `DATA_MODEL.md`,
  `BUILD_ORDER.md`, `FEATURES.md`, `WORKFLOW.md`, `ARCHITECTURE.md`, `SETUP.md`, `CLAUDE.md`.
- 45 tests (was 23); ruff/mypy clean on everything touched.

**In progress:** Nothing half-built. M8 is complete and measured.

**Key decisions:**
- **Build the measurement before the feature** → a saturated benchmark reports nothing either
  way; the graph bucket + coverage metric had to exist first (A28/A29).
- **Coverage alongside the booleans, not replacing them** → single-location keys make coverage
  arithmetically identical to the boolean, so every historical number stays comparable and old
  checkpoints backfill exactly.
- **No ambiguous tier** → a wrong edge sends the agent to unrelated code; a missing one only
  leaves it searching. ARISE's finding, confirmed on `requests`' duplicated `request` (B24).
- **One `find_references` tool, not `get_callers` + `get_callees`** → shared implementation,
  flat prompt prefix, and `list_dependencies` is in-repo proof the shape gets called correctly.
- **Tree-sitter name matching over LSP/SCIP** → precise resolution needs a build or a per-repo
  language server; we clone arbitrary repos with no deps installed. stack-graphs named as the
  V2 upgrade path (B26).
- **Its own question bucket** → judged on the old 46 the tool moves nothing and would have been
  deleted; it adds a capability rather than improving one (B27).
- **Narrow answer keys left as is** (user's call) → score quoted as a lower bound. A26 now
  carries the trap: the `metadata.mdx` question is a `docs` guard-rail and must **not** be
  widened, or the docs alarm silently stops firing.
- **Bracket-strict citations left as is** (user's call) → loosening trades false negatives for
  false positives.

**Next step:** **M9 — metrics + dashboard**, the last V1 milestone: `metrics` pipeline stage,
`metric` rows, an API endpoint, and the React dashboard (file/function counts, LOC, language
breakdown, largest files). It is also the only thing that makes `status = READY` reachable —
the pipeline currently terminates at `embedding`.

**Watch out for:**
- **`.env.example` has an uncommitted change** (`LANGSMITH_PROJECT` blanked) made outside this
  session — revert or keep, but it is sitting in the working tree.
- **LangSmith tracing was disabled by the user this session.** It had been pointing at the EU
  endpoint, timing out from the container, flooding stderr and inflating the eval's seconds
  column. Re-enable only with a reachable endpoint.
- **Module-level call sites are dropped** — `reference_edge.from_entity_id` is not nullable, so
  a JS/TS `export const x = f()` has no caller and is skipped. Caps one zod eval question at
  `ccov` 0.67. Documented with its trigger in B24, `DATA_MODEL.md`, and the
  `enclosing_entity_index` docstring. Deliberate, not an oversight.
- **Re-seeding is mandatory after any change to `indexer/calls.py`** — edges are built at index
  time (`eval.seed --force --repos all`, ~2 min, a few cents).
- **`eval/out/*.jsonl` now holds five tags** from this session (`m8-graph-baseline`,
  `m8-graph-on`, `m8-graph-off`, `m8-regression`, `m8-refmap`) — re-scoring against widened
  keys needs no API calls.
- Git Bash heredocs with quoted bodies still fail intermittently on this machine; write scripts
  to a file (Write tool) and run them. Python on Windows does not see Git Bash's `/tmp` — use
  the scratchpad path.
- Docker Desktop crashed mid-session and had to be restarted; containers were left running.
- Pre-existing, untouched: 3 ruff findings (`api/auth.py`, init migration), mypy celery stubs
  and `core/github.py:70`, `retry_repository` still only accepts `FAILED`.

---

## Session — 2026-09-06 18:01

**Worked on:** Reviewing, verifying and committing the uncommitted M9 (metrics + dashboard)
working tree; correcting a long-standing false claim about Alembic autogenerate; restructuring
the repo workspace layout. Five commits on `develop` (`1e11388`…`1d3447a`). Working tree clean.
**Done:**
- **Verified M9 before committing:** `alembic current` = `e9f0a1b2c3d4` (head, applied),
  `metrics` table present with real rows for 2 repos, `pytest` 49 passed, `pnpm build` clean,
  `mypy core/models.py` clean. Only the 3 pre-existing ruff findings remain, untouched.
- **Alembic correction (`2d043a4`).** The comment repeated across five migrations —
  "autogenerate can't see the raw-SQL GIN indexes" — was **wrong**. Proved it both ways with
  throwaway revisions: without the declarations autogenerate emits two `drop_index` calls;
  with `Index(..., postgresql_using="gin")` on `File` and `Chunk` it emits `pass`. Added the
  two declarations (no schema migration needed — the indexes already exist) and corrected
  seven migration comments in place. `CONCEPTS.md` A32.
- **Layout restructure (`fa8f6b3`).** The workspace had three columns (file tree │ conversation
  sidebar │ messages). Now one full-width `<header>` holds logo, repo name, Dashboard/Chat
  tabs, a conversation dropdown and `+ New chat`; below it exactly two panels. New
  `web/src/components/ConversationMenu.tsx` (dropdown, closes on outside click + Escape);
  `ChatPanel` is now controlled (`activeId` / `onActiveIdChange`), its sidebar and its
  conversation-list query deleted; `RepoExplorer` owns `conversationId`. `CONCEPTS.md` B30.
- **Explained (no code):** why the migration is needed at all, what reverting the session-cookie
  change would and would not break, and exactly which metrics cover only py/js/ts.
**In progress:** Nothing half-built. Everything committed, tree clean.
**Key decisions:**
- Declare the GIN indexes in the models rather than keep hand-writing migrations → the rule is
  *autogenerate, then review*; an empty autogenerated revision is a passing test we had been
  discarding. Applied migrations' `upgrade()`/`downgrade()` bodies were **not** touched, only
  their comments — rewriting applied history is a separate, riskier thing.
- Chat actions render only on the Chat tab → on Dashboard they would change nothing visible.
  One `tab === 'chat' &&` guard reverses it.
- Header is pinned by layout (`h-screen` + `overflow-hidden` + `shrink-0`), not `position:
  sticky` → nothing scrolls past it, so sticky would be inert CSS.
- One frontend commit rather than two → splitting would have left a non-building intermediate
  commit, since `RepoExplorer` imports both `RepoDashboard` and `ConversationMenu`.
**Next step:** Manual browser check of the new header layout — it was never opened in a browser
this session, only type-checked and built. After that, V1 is complete: either the deployment
work in `DEPLOYMENT.md` (Terraform + EC2, nothing built yet) or the M7 eval caveats
(`RETRIEVAL.md` — widen the 3 narrow answer keys, cents/question accounting, an 800-token map).
**Watch out for:**
- **The five commits carry `Co-Authored-By: Claude Opus 5` + `Claude-Session` trailers**, per
  this session's attribution config, which contradicts the standing "no co-author trailer"
  preference. Nothing is pushed — rewritable with an interactive rebase if unwanted.
- **`.env` needs `SESSION_MAX_AGE_SECONDS` / `SESSION_HTTPS_ONLY`** to match the new
  `.env.example`; both have safe defaults in `core/config.py`, so nothing breaks without them,
  but `SESSION_HTTPS_ONLY` **must** be `true` before any TLS deployment.
- `same_site="lax"` in `api/main.py` is load-bearing — "tightening" it to `strict` silently
  breaks GitHub OAuth, because the callback arrives as a cross-site top-level GET.
- Python run from Git Bash cannot write to `/c/...` paths (hit again this session) — use
  Windows-style paths or the scratchpad. Heredocs with quoted bodies still work for `cat >>`
  but the Write tool is safer for TSX.
- Docker services (`db`, `redis`, `api`, `worker`) left running; the frontend dev server was
  never started.
