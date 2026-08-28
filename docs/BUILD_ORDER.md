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
M5, and it's what makes M6 and M7 each reportable as its own delta instead of one vague
"retrieval got better."

Cost order got us here — lexical was cheap to build and cheap to throw away, so it went
first and the eval set landed before pgvector. That worked: `file.content` is persisted at
clone time (M3), so a `tsvector` index over it was one migration and zero pipeline cost,
which was enough retrieval to build the whole citation path against.

**Semantic retrieval is no longer conditional.** It was gated on "only if the eval proves
lexical is failing" because embeddings were expensive to build *and* expensive to redo. On
Gemini's free tier the cost half of that argument is gone, and the product decision is made:
agentic RAG needs a semantic leg for the conceptual class of question. The eval stops being
a *gate* and goes back to being a *scoreboard*.

## Status

**Done:** M1 Skeleton · M2 Auth · M3 Import + clone · M4 Parse + extract · M5 Eval + hybrid
search + AST chunking. **M6 (Gemini provider layer + semantic leg): code complete, recall
delta not yet measured** — the free tier's daily embedding quota (1,000 requests) was
exhausted mid-measurement; needs one clean full re-seed + eval run once quota resets or
billing is enabled. **Next up: finish M6's measurement, then M7.**

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

6. ~~**Gemini provider layer + the semantic leg.**~~ **Code done, measurement pending.**
   `core/ai` (the only module that imports any provider SDK — Gemini for embeddings, plus a
   switchable chat factory covering OpenAI/Anthropic for M8 testing) with a batched,
   rate-limited, resumable embedding client. `chunk.embedding vector(1536)` — **no index**
   in V1, exact cosine scan instead (see `DATA_MODEL.md`'s deferred-and-why) — an
   `embedding` pipeline stage (non-fatal on failure, see `WORKFLOW.md`), and
   `semantic_search()` fused with lexical via the RRF already written in `fusion.py`.
   Chunking shipped in M5, so this was purely the embedding leg.
   **Ends with a measured recall delta against the pinned lexical-only baseline** — blocked
   for now on the free tier's daily embedding quota; see `RETRIEVAL.md`.

7. **The graph leg.** Call-graph extraction — a second Tree-sitter walk that *does* descend
   into function bodies (the existing one deliberately stops there), resolving callee names
   against the symbol table into a new `reference_edge` table. Then `expand(seeds, max_hops)`
   over `reference_edge ∪ dependency_edge`, seeded from the top lexical + semantic hits and
   fused in as a third RRF leg — and the same traversal exposed as `get_callers` /
   `get_callees` / `list_dependencies` agent tools.
   **Ends with an in/out ablation**, the same one that cut structural retrieval in M5.

8. **The agent.** Repo map (PageRank-ranked, AI-free table of contents from `code_entity` +
   `dependency_edge`, in the stable prompt prefix so the first move is informed rather than a
   blind keyword guess) + a **hand-rolled LangGraph `StateGraph`** — we write the state,
   `call_model`, `ToolNode`, and the `should_continue` edge; LangGraph only runs the graph.
   Streamed over SSE with tool-call status, citations built from tool-result metadata. Chat
   UI reuses the existing citation → Monaco overlay path. Extends `eval/run.py` to score
   agent-mediated retrieval (resumable — free-tier chat quota makes a full run a day's work).
   - **Two hallucination-mitigation steps, both cheap.** A CRAG-style retrieval grade runs
     right after each tool call, before that result reaches the model: junk/irrelevant tool
     output triggers a re-search instead of being handed to `call_model` to generate from. A
     citation-verification node runs right before `END`: every `file:line` in the draft
     answer is checked against the tool-result metadata actually collected this turn, and any
     citation that isn't backed by a real retrieval hit is dropped. Neither costs an extra
     model call — see `RETRIEVAL.md`'s "Hallucination mitigation" section for why an
     LLM-judge faithfulness check (a real SOTA option) is deliberately deferred until the eval
     shows these two aren't enough.

9. **Basic metrics + dashboard.** `metrics` pipeline stage; status finally reaches `READY`.

## Two naming traps

**"Structural" is overloaded.** The thing **cut** in M5 was trigram symbol-*name* lookup
over `code_entity`. The thing being **built** in M7 is graph *traversal* over call and import
edges — different data, different input (a location, not a query), never built at any layer.
The M5 cut decision doesn't apply to it. See `RETRIEVAL.md`'s decision record.

**There is no "chat v1 then chat v2".** That split collapsed into a single milestone — see
`FEATURES.md` §4 for why shipping the agent directly is the smaller piece of work, not the
larger one.
