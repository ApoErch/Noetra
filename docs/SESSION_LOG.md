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
