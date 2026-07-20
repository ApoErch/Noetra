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

## 3. AI Chat  *(primary feature)*
- Natural-language questions: "Explain authentication", "Where is Redis used?",
  "How do payments work?"
- **LangGraph agent** with retrieval tools (`code_search`, `find_symbol`, `read_file`,
  `list_dependencies`). Iterates like a developer exploring the repo. See `RETRIEVAL.md`.
- Streamed answers, each citing concrete `file:line` locations rendered as links into Monaco.
- Build note: ships first as **single-shot RAG** (milestone 7), then upgrades to the
  agent (milestone 8). Same retriever underneath.
- Optionally persists history in `chat_message`.

## 4. Semantic / hybrid search
- Replaces manual Ctrl+Shift+F. "where do we send emails?", "where is `createToken` defined?"
- Hybrid retrieval: lexical + structural + semantic, RRF-fused (`RETRIEVAL.md`).
- Ranked results with `file:line` + one-line context. Click → open in Monaco at that line.

## 5. Repository dashboard  *(basic metrics only)*
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
