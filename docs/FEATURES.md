# Features

Each V1 surface, what it does, what it needs from the backend. V2 non-goals at the end.

## 1. Auth & repo list
- GitHub OAuth login; persist user + encrypted token.
- Landing dashboard: my repositories, recent analyses.
- Repo list pulled live from the GitHub API for import selection.

## 2. Import
- Two sources: public GitHub URL, private repo via OAuth.
- Creates `repository(status=queued)` and enqueues a Celery indexing job.
- UI shows live indexing status/progress until `ready`.

## 3. File tree browser
- Available as soon as `status` passes `cloning` — doesn't wait on parsing/chunking/embedding.
- VS Code-style sidebar: nested tree built client-side from a flat list of `file.path`
  (`GET /repos/{id}/files`, paths only, no content — cheap even at tens of thousands of files).
- Click a file → lazy-fetch its content (`GET /repos/{id}/files/{file_id}`) and open in Monaco.
  Binary files (`file.is_binary`) show a "can't preview this" state instead of content.
- Entry point independent of search/chat citations — this is direct browsing, not just
  jump-to-citation.

## 4. AI Chat  *(primary feature — this is the agentic RAG surface)*
- Natural-language questions: "Explain authentication", "Where is Redis used?",
  "How do payments work?"
- **LangGraph agent** with five retrieval tools: `code_search`, `read_file`,
  `list_dependencies`, `get_callers`, `get_callees`. Iterates like a developer exploring the
  repo. See `RETRIEVAL.md`.
- **The agent picks its own strategy per query** — that's the actual feature, not "chat with
  a retriever bolted on." An exact-identifier question resolves in one `code_search` and
  stops. A vague conceptual one searches, expands the top hit through the call graph, reads
  a file, and searches again with better terms if the first pass came back thin.
- Loop is a **hand-rolled `StateGraph`** (state → `call_model` → `should_continue` →
  `ToolNode` → back), not `create_react_agent`. We need custom nodes for citation collection
  and repo-map priming anyway.
- Oriented by an AI-free **repo map** — a PageRank-ranked table of contents (top symbols per
  file, ranked by import-graph centrality) in the *stable* prompt prefix, so the agent's
  first search is informed rather than a blind guess. Built in milestone 8 from
  `code_entity` + `dependency_edge`. See `RETRIEVAL.md`.
- Streamed answers over SSE, each citing concrete `file:line` locations rendered as links
  into Monaco — reusing the citation → overlay path search already proved. Citations are
  built from tool-result metadata, never from what the model says it read.
- Stream tool-call status alongside tokens ("searching `TokenService`… reading
  `auth/tokens.py`…"). An agent loop takes seconds; silence during it reads as a hang.
- Build note: ships **directly as the agent** (milestone 8) — there is no single-shot RAG
  version. Once the retrievers are already exposed as tools, the multi-step loop is a small
  amount of code on top of them, and a single-shot version would be deleted a week later.
  The earlier plan's "chat v1 then chat v2" split was extra work, not less.
- `find_symbol` is **not** in the tool list — it was structural v1's agent-facing form, and
  went when structural v1 was cut. `code_search` covers the same ground per the eval.
- Optionally persists history in `chat_message`.

## 5. Hybrid search
- Replaces manual Ctrl+Shift+F. "where do we send emails?", "where is `createToken` defined?"
- Hybrid retrieval: lexical + semantic RRF-fused, plus a graph leg seeded from their top
  hits (`RETRIEVAL.md`). No reranker — none is available free; see `RETRIEVAL.md`.
- Ranked results with `file:line` + one-line context. Click → open in Monaco at that line.
- Build note: shipped in milestone 5 with **lexical + structural v1**, then structural v1 was
  removed after an eval-driven ablation showed it moving recall@5 by only +0.04 — see
  `RETRIEVAL.md`'s decision record. Milestone 6 added the semantic leg, fused via RRF (its
  recall delta is still being measured — `RETRIEVAL.md`); the graph leg lands in milestone 7.
  **The endpoint contract and the UI don't change through any of that** — only what's behind
  `core/retrieval` does. Shipping search early is what proved the citation path end-to-end
  and gave the eval harness something to measure.
- Lexical results are available once `status` passes `chunking`; the fused semantic leg needs
  `embedding` to finish. The file tree unlocks earlier, right after `cloning`.

## 6. Repository dashboard  *(basic metrics only)*
- Header counts: file count, function count, total LOC.
- Language breakdown (python / js / ts).
- Largest files.
- Data comes from `repository` + the `metric` rows. **Nothing beyond these in V1** —
  no complexity, dead code, duplicates, ownership, or health score.

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
