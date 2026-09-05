# Build order

The sequence in which the code gets written over the life of the project. Each milestone
runs end-to-end before the next one starts.

**Don't confuse this with the pipeline order** (`cloning → parsing → graphing → chunking →
embedding → metrics`), which is what happens during a single indexing run of one repo,
every time — see `WORKFLOW.md`. They're separate decisions that happen to follow the same
principle: *cheap and deterministic first, slow and expensive last.* In the pipeline that
means the symbol table, graphs, and chunks all finish before embedding starts, so a repo
becomes searchable minutes into indexing rather than only when the slowest stage completes.

## Ordering principle

**One retrieval leg at a time, and each one ends with a measured `recall@k` delta.** If
several fused retrievers return junk you cannot tell which one is at fault, so every leg
lands against a clean pinned baseline. That's what made the structural ablation possible in
M5, and it's what made M6 reportable as its own delta instead of a vague "retrieval got
better."

**The graph is not a fused leg, so it ships *after* the agent.** Graph traversal answers
"what's connected to this location?", not "what's relevant to this query?" — it's exposed as
tools the agent calls, never voted into RRF (`RETRIEVAL.md`). The only place it can be
measured is inside the agent loop (tools on vs. off), so building it before the agent would
mean shipping it unmeasured. Agent first, graph second.

Cost order got us here — lexical was cheap to build and cheap to throw away, so it went
first and the eval set landed before pgvector. That worked: `file.content` is persisted at
clone time (M3), so a `tsvector` index over it was one migration and zero pipeline cost,
which was enough retrieval to build the whole citation path against.

**Semantic retrieval is no longer conditional.** It was gated on "only if the eval proves
lexical is failing" because embeddings were expensive to build *and* expensive to redo. At
~$0.02 per million tokens the cost half of that argument is gone, and the product decision
is made: agentic RAG needs a semantic leg for the conceptual class of question. The eval
stops being a *gate* and goes back to being a *scoreboard*.

## Status

**Done:** M1 Skeleton · M2 Auth · M3 Import + clone · M4 Parse + extract · M5 Eval + hybrid
search + AST chunking · **M6 provider layer + semantic leg — measured 2026-09-04** on OpenAI
after the Gemini free tier blocked the first attempt (`CONCEPTS.md` A20): semantic-only
`recall@5` 0.85 vs the 0.72 lexical baseline; fused 0.85 / 0.91 after re-tuning RRF to
`k=10` with a 2× semantic weight (A22) — beating either leg alone · **M7 the agent —
shipped and measured 2026-09-05**: hand-rolled LangGraph loop, three tools, repo map,
mechanical citation verifier, SSE chat UI; agent eval 42/46 cited on `gpt-5.4-mini` (≈45/46 after key review)
(`RETRIEVAL.md`). **Next up: M8 — the call graph as agent tools**, measured on that eval
with the tools on vs. off.

Per-milestone writeups live in `LEARNING_LOG.md`.

## Milestones

1. ~~**Skeleton** — monorepo, Docker Compose, Alembic wired end-to-end.~~ **Done.**
2. ~~**Auth** — GitHub OAuth, session cookies, encrypted token at rest.~~ **Done.**
3. ~~**Import + clone** — validate URL, enqueue Celery job, authenticated shallow clone,
   persist a `file` row per tracked path.~~ **Done.**
4. ~~**Parse + extract** — Tree-sitter py/js/ts → files + entities (the symbol table), plus
   the two things that ride along free with it: the **lexical index** (`tsvector`) and the
   **dependency graph** (resolve the imports the parser already extracted). No AI calls in
   this milestone at all.~~ **Done.**
5. ~~**Eval harness** (questions with known answer locations → `recall@k`) + search endpoint
   & UI. First end-to-end `file:line` citations, and the scoreboard every later retrieval
   change is judged against.~~ **Done** — 46 questions.
   - **AST chunking was pulled forward from M7 into this milestone** — it earns its place
     through exact citations and smaller agent payloads, independently of embeddings, and it
     is a prerequisite for them anyway. The pipeline now runs
     `cloning → parsing → graphing → chunking`.
   - **Structural retrieval was subsequently removed** after an ablation against this same
     eval set showed it moving recall@5 by only +0.04, concentrated in one edge case — see
     `RETRIEVAL.md`'s decision record.
   - `search()` is lexical-only, and **the baseline M6 must beat is line-level `recall@5`
     0.72 / `recall@20` 0.78**. (0.76 / 0.81 was the pre-removal hybrid number — don't quote
     it as the current one.)

6. ~~**Provider layer + the semantic leg.**~~ **Done — measured 2026-09-04.**
   `core/ai` (the only module that imports any provider SDK — OpenAI `text-embedding-3-small`
   for embeddings behind an `EMBEDDING_PROVIDER` switch, plus a chat factory covering
   OpenAI/Anthropic for M7 testing) with a resumable embedding client.
   `chunk.embedding vector(1536)` — **no index** in V1, exact cosine scan instead (see
   `DATA_MODEL.md`'s deferred-and-why) — an `embedding` pipeline stage (non-fatal on
   failure, see `WORKFLOW.md`), and `semantic_search()` fused with lexical via the RRF
   already written in `fusion.py`. Chunking shipped in M5, so this was purely the embedding
   leg. Built first on Gemini's free tier; moved to OpenAI after the quotas made the
   measurement impossible.
   **Measured against the pinned lexical-only baseline:** conceptual `@5` 0.36 → 0.64,
   overall 0.72 → 0.85. Fusion needed two measured fixes to beat semantic alone (`k=10`,
   semantic weighted 2×) — see `RETRIEVAL.md`.

7. ~~**The agent.**~~ **Done — measured 2026-09-05.** `core/agent/`: tools `code_search`,
   `read_file` (≤200 lines/call), `list_dependencies` (imports / imported_by); a
   PageRank-ranked repo map in the stable prompt prefix; a hand-rolled `StateGraph`
   (`call_model → tools → … → verify_citations`) with a tool budget and a no-progress guard;
   SSE chat endpoint + `chat_conversation`/`chat_message` tables (own tables, not a
   checkpointer — `CONCEPTS.md` B22); chat UI replacing the search panel, citations clicking
   through to the Monaco overlay. `eval/agent.py` scores retrieved / cited / cost per
   question — **the baseline M8 is measured against**: gpt-5.4-mini 42/46 cited (3 of the 4 misses are narrow answer keys — `CONCEPTS.md` A26),
   3.3 tool calls/question, 0 stripped citations across 138 answers.
   - **What changed from the plan:** the router node and the CRAG-style grader node were
     dropped before building (`RETRIEVAL.md`, `CONCEPTS.md` B21) — in a tool-calling loop the
     model is already the grader, and the off-topic branch is a zero-tool answer. The
     citation verifier stayed, as pure code.
   - Default chat model moved `gpt-4o-mini` → `gpt-5.4-mini` on the eval (cited 0.59 → 0.91 on 46 questions; mostly citation-format compliance).

8. **The call graph, as agent tools.** A second Tree-sitter walk that *does* descend into
   function bodies (the symbol-table walk deliberately stops there) extracts call sites;
   callee names resolve against the symbol table into `reference_edge`, each edge carrying a
   **confidence** from its resolution tier (same file → imported file → unique repo-wide →
   ambiguous). Exposed as `get_callees(entity)` and `get_callers(entity)` — one hop per
   call; the agent hops again if it wants to. **Never a fused leg** — `RETRIEVAL.md` has the
   research and the reasoning.
   **Measured two ways:** an edge-quality eval (hand-labelled call sites at the pinned
   `noetra` SHA → precision/recall of resolved edges), and the M7 agent eval run with the
   graph tools on vs. off — the LocAgent-style ablation, which is the only place the graph's
   value actually shows up.

9. **Basic metrics + dashboard.** `metrics` pipeline stage; status finally reaches `READY`.

## Two naming traps

**"Structural" is overloaded.** The thing **cut** in M5 was trigram symbol-*name* lookup
over `code_entity`. The thing being **built** in M8 is graph *traversal* over call and import
edges — different data, different input (a location, not a query), never built at any layer.
The M5 cut decision doesn't apply to it. See `RETRIEVAL.md`'s decision record.

**There is no "chat v1 then chat v2".** That split collapsed into a single milestone — see
`FEATURES.md` §4 for why shipping the agent directly is the smaller piece of work, not the
larger one.
