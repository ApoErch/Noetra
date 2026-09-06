# Features

Each V1 surface, what it does, what it needs from the backend. V2 non-goals at the end.

## 1. Auth & repo list
- GitHub OAuth login; persist user + encrypted token.
- Landing dashboard: my repositories, recent analyses.
- Repo list pulled live from the GitHub API for import selection.

## 2. Import
- Two sources: public GitHub URL, private repo via OAuth.
- Creates `repository(status=queued)` and enqueues a Celery indexing job.
- UI shows live indexing status until `ready`: the stage in words, a step counter, and a
  progress bar derived from the stage's position in the pipeline (there is no `progress`
  column — the ordinal answers the same question for free).
- Two recovery paths, and they are not the same operation. **Retry** on a `failed` repo
  deletes its files and re-clones. **Resume** on a repo that stopped at `embedding`/`metrics`
  re-runs only the pipeline's tail and keeps the index it already paid for. Resume is offered
  only when no job holds the repo's Redis index lock (`is_indexing` on `GET /repos`).
- A repo can be removed at any status, including while it is still `queued` or `cloning`.

## 3. File tree browser
- Served by the API as soon as `status` passes `cloning`, but reached through the repo
  workspace, which opens at `ready` (`WORKFLOW.md`, `CONCEPTS.md` B28).
- VS Code-style sidebar: nested tree built client-side from a flat list of `file.path`
  (`GET /repos/{id}/files`, paths only, no content — cheap even at tens of thousands of files).
- Click a file → lazy-fetch its content (`GET /repos/{id}/files/{file_id}`) and open in Monaco.
  Binary files (`file.is_binary`) show a "can't preview this" state instead of content.
- Entry point independent of search/chat citations — this is direct browsing, not just
  jump-to-citation.

## 4. AI Chat  *(primary feature — this is the agentic RAG surface; shipped in milestone 7)*
- Natural-language questions: "Explain authentication", "Where is Redis used?",
  "How does the app stop two indexing jobs running for the same repo?"
- **LangGraph agent** with four tools: `code_search` (the fused lexical + semantic
  `search()`), `read_file` (≤200 numbered lines per call), `list_dependencies` (imports /
  imported by), and `find_references` (callers / callees over the call graph, milestone 8).
  See `RETRIEVAL.md`.
- **`find_references` answers what search structurally cannot.** `code_search` returns at
  most two hits per file, so "every place this function is used" is out of its reach by
  construction; the graph tool returns the complete set, and resolves which definition you
  meant when a name is defined twice. Measured: coverage 0.63 to 0.84 on enumeration
  questions, with 28% fewer tool calls.
- **The agent picks its own strategy per query** — that's the actual feature. Measured: an
  identifier question ends after one search + one read (2 calls, ~5 s); a conceptual one
  searches, reads two files, and answers (3–5 calls). An off-topic question ends with zero
  tool calls — the system prompt declines and redirects; there is no separate router.
- Loop is a **hand-rolled `StateGraph`** (`call_model → tools → … → verify_citations`), not
  `create_react_agent`, with a tool budget (`AGENT_TOOL_BUDGET`) and a no-progress guard.
- Oriented by an AI-free **repo map** — PageRank-ranked top symbols per file in the *stable*
  prompt prefix. Measured to keep the model grounded: without it, gpt-5.4-mini answered one
  question from memory with no tool calls at all.
- Streamed over SSE (`POST /repos/{id}/conversations/{cid}/messages`): tool-call status
  events (`searched "…" — 10 hits`, `read tasks.py:41-88`) then answer tokens, then the
  verified citations. The UI shows the steps live and keeps them as a collapsible block.
- Citations are written inline as `[path:start-end]` and rendered as chips → the same
  Monaco overlay path search proved. Every one is **verified in code** against the ranges
  the tools actually returned that turn; unbacked ones are demoted to plain text. Zero
  stripped across 64 eval answers so far.
- **Conversations persist**: `chat_conversation` + `chat_message` (own tables, not a
  LangGraph checkpointer — `CONCEPTS.md` B22). Each turn replays only the last 12
  user/assistant messages, never old tool results. New chat / switch / delete in the panel.
- The endpoint unlocks once `chunking` has finished — it gates on "does this repo have
  chunks", never on `status`, so the agent works lexical-only until embeddings exist. The
  chat *tab* is nonetheless reached through a workspace that opens at `ready`.
- Planned-then-dropped: a CRAG-style retrieval-grade node and an on/off-topic router node —
  see `RETRIEVAL.md` for the evidence and `CONCEPTS.md` B21 for the revisit triggers.

## 5. Hybrid search  *(now internal — the agent's `code_search` tool)*
- Lexical + semantic, RRF-fused — two legs, full stop. The call graph is an agent tool, not a
  search leg (`RETRIEVAL.md`). No reranker in V1.
- History: shipped in milestone 5 behind `GET /repos/{id}/search` with a search panel, which
  proved the citation → Monaco path end to end and gave the eval something to measure.
  Structural v1 was removed after an ablation (+0.04); milestone 6 added the semantic leg
  (recall@5 0.72 → 0.85). **In milestone 7 the endpoint and panel were removed** — chat
  replaced them as the user-facing surface, and `eval/run.py` calls `search()` directly. The
  retrieval module is unchanged; only its caller moved.

## 6. Repository dashboard  *(basic metrics only; shipped in milestone 9)*
- A tab in the repo workspace, next to Chat. Stat tiles: files (total, and how many are
  source), functions (with the method count), classes, and total lines of code.
- Language breakdown (python / js / ts) as a share-of-lines bar with a labelled legend.
- Largest source files, ranked, each clickable straight into the Monaco overlay.
- **The numbers say "source" where they mean source.** `file.language` and `file.loc` are
  only populated for the parsed languages, so LOC, the breakdown and the largest-files list
  exclude Markdown, JSON, config and binaries — which `file_count.total` does count. Showing
  both totals is the difference between a caveat and a wrong number.
- Data comes from the `metric` rows written by the pipeline's `metrics` stage, not from a
  count run per request — a 36,000-file repo would otherwise re-aggregate on every poll.
- **Nothing beyond these in V1** — no complexity, dead code, duplicates, ownership, or
  health score.

---

## V2 — deferred (do NOT build in V1)

Leave clean seams; no implementation.

- **Security scanner** — hardcoded secrets, exposed keys, dangerous SQL, unsafe `eval`,
  weak JWT, dependency vulnerabilities. (`security_finding` table added in V2.)
- **Architecture graph view** — React Flow visualization of `dependency_edge`
  (file + module level). The edge *data* is already built in V1; only the view is deferred.
- **AI Code Review** — per-file issue detection (nested loops, injection risk, missing
  null checks, naming, performance), each linked to code.
- **Pull Request Review** — paste a PR URL → summary, files changed, potential bugs,
  breaking changes, suggested tests, risk score.
- **Advanced metrics** — dead-code candidates, duplicate code, unused imports, longest
  methods, complexity, code ownership.
- **More languages** — beyond python/js/ts.
