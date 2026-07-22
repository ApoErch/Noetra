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

## 4. AI Chat  *(primary feature)*
- Natural-language questions: "Explain authentication", "Where is Redis used?",
  "How do payments work?"
- **LangGraph agent** with retrieval tools (`code_search`, `find_symbol`, `read_file`,
  `list_dependencies`). Iterates like a developer exploring the repo. See `RETRIEVAL.md`.
- Streamed answers, each citing concrete `file:line` locations rendered as links into Monaco.
  Citations are built from tool-result metadata, never from what the model says it read.
- Build note: ships **directly as the agent** (milestone 6) — there is no single-shot RAG
  version. Once the retrievers are already exposed as tools, the multi-step loop is a small
  amount of code on top of them, and a single-shot version would be deleted a week later.
  The earlier plan's "chat v1 then chat v2" split was extra work, not less.
- Stream tool-call status alongside tokens ("searching `TokenService`… reading
  `auth/tokens.py`…"). An agent loop takes seconds; silence during it reads as a hang.
- Optionally persists history in `chat_message`.

## 5. Hybrid search
- Replaces manual Ctrl+Shift+F. "where do we send emails?", "where is `createToken` defined?"
- Hybrid retrieval: lexical + structural + semantic, RRF-fused, then reranked
  (`RETRIEVAL.md`).
- Ranked results with `file:line` + one-line context. Click → open in Monaco at that line.
- Build note: ships in milestone 5 with **lexical + structural only** and gains the
  semantic leg in milestone 7. The endpoint contract and the UI don't change between the
  two — only what's behind `core/retrieval` does. Shipping it early is what proves the
  citation path end-to-end and gives the eval harness something to measure.
- Lexical results are available as soon as `status` passes `cloning`, the same as the file
  tree; symbol lookup needs `parsing`; the semantic leg needs `ready`.

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
