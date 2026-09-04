# Retrieval (the core)

Noetra uses **agentic RAG**: three retrieval legs that answer different kinds of question, and
an agent that decides which to use, in what order, and how many times. The agent is only as
good as what this returns.

**Agentic** means retrieval is not a fixed pre-LLM step — the model calls it mid-reasoning and
iterates. An exact identifier resolves in **one** `code_search` and never touches the vector
index. A vague conceptual question may search, expand the top hit via `get_callers`, then
search again with better terms. That per-query branching is what separates it from
RAG-with-extra-steps.

## The three legs

| Leg | Answers | Status |
|---|---|---|
| **Lexical** — Postgres full-text over chunk `embed_text` | identifiers, error strings, config keys | shipped (M5) |
| **Semantic** — pgvector cosine over the same chunks | questions whose words appear *nowhere* in the code | shipped (M6), recall delta pending |
| **Graph** — traversal over call + import edges | "who calls this?", "what does this import?" | M7 |

**Lexical.** Two passes: strict `websearch_to_tsquery` (which AND-joins bare terms), then an
OR-relaxed rewrite backfilling unused slots — strict hits keep their positions, so relaxation
cannot cost precision. A per-file cap of 2 chunks stops one verbose file taking every slot;
a 0.3× `ts_rank` penalty where `language IS NULL` stops prose *about* code outranking the code.

**Semantic.** The only leg that earns its keep when the user's words appear nowhere in the
codebase — a real class, but a **minority** of code questions. Most of the time the identifier
you want is literally in the file, which is why lexical carries more weight than vector-first
intuition suggests. *"how does authentication work?"* → `core/security.py`, which never says
"authentication". Mirrors lexical's SQL shape (same per-file cap, emits the chunk's own line
range verbatim — see the key-alignment trap below), `match_line=None` because a semantic-only
hit usually contains none of the query's words.

**Graph.** Takes **locations**, returns **connected locations**. Used two ways — a third RRF
leg seeded from the other two, and direct agent tools. Details below.

**Naming trap.** "Structural" has meant two things. *Structural v1* — trigram symbol-**name**
lookup over `code_entity` — shipped in M5 and was **cut** after an ablation showed +0.04
`recall@5`, all from one `$`-prefixed identifier the tokenizer mangles. *Graph traversal* is
different data (`reference_edge`, not `code_entity.name`) and different input (a location,
not a query); the v1 cut says nothing about it.

## Chunking rule (non-negotiable)

Chunk by **AST node** via Tree-sitter — never fixed token count. One chunk per **leaf entity**,
plus **gap chunks** covering everything between, so no line is unsearchable. A chunk that
splits a function in half poisons retrieval.

Embed the chunk with its context prefixed:

```
src/auth/tokens.py › class TokenService › def refresh(self, token: str) -> Token
<the actual chunk source>
```

A bare `def refresh(self, token)` could be a cache, a session, or an OAuth token; the prefix
lands the vector near "authentication" instead of somewhere generic. This is **contextual
retrieval** for the price of an f-string. Stored as `chunk.embed_text`; raw `chunk.content`
goes back to the model. The lexical `tsvector` generates over `embed_text` too.

## The embedding model

**OpenAI `text-embedding-3-small`**, via `core/ai/embeddings.py` — the only module that
imports a provider SDK. `EMBEDDING_PROVIDER` + the API key are the entire switch.

- **1536 dims natively** — matches `chunk.embedding vector(1536)`; no truncation parameter.
- **Vectors arrive unit-normalized**, so cosine distance is correct as-is.
- **Input cap ~8,191 tokens.** The API *rejects* over-long input rather than silently
  truncating, so `core/ai` truncates at 24,000 chars (~3 chars/token for code) first.
- **Rate limits are not a design constraint** on a paid tier. The embedding stage sends one
  request per page of 200 chunks with no pacing or retry tuning; if 429s ever appear, batch
  size is the knob to revisit. `chunk.embedding` is still **nullable** and the stage selects
  `WHERE embedding IS NULL`, so a crash or provider outage mid-repo resumes instead of
  restarting.

Switching embedding providers means a full re-embed (a different model is a different vector
space), so old vectors are wiped, never mixed.

## The graph leg (M7)

`dependency_edge` is the **file-level import graph**. `reference_edge` adds the **call graph**:
which entity calls which, resolved **by name** against the symbol table — same-file first,
then imported-file candidates, all candidates when genuinely ambiguous. No type inference:
the standard "poor man's call graph" (ctags, aider), roughly right and enough to orient an
agent that then reads the real file.

### As a fusion leg (seeded expansion)

```
query ──┬── lexical  ──> top N ──┐
        └── semantic ──> top N ──┴──> resolve chunks → entity_ids (chunk.entity_id)
                                             │
                                    expand(seeds, max_hops=2)
                                    over reference_edge ∪ dependency_edge
                                    hop-ranked; ties broken by # of seeds reaching it
                                             │
  RRF([lexical, semantic, graph]) ───────────┘  ──> final ranked hits
```

Gap chunks have `entity_id = NULL` and don't seed — they still reach the result via the
other legs.

**Key-alignment trap.** `fusion.py` dedupes on `(file_id, start_line, end_line)`. If the
graph leg emits *entity* ranges while the others emit *chunk* ranges, keys never collide, RRF
never fuses anything, and it degenerates into concatenation **that still looks like it's
working**. Every expanded entity must map back to its chunk before becoming a `RetrievalHit`.

**The cost, stated honestly.** Seeding from the other legs **couples the retrievers**: a wrong
seed is reinforced by its neighbours instead of cancelled by an independent leg — the opposite
of what RRF over independent retrievers buys. Accepted deliberately; M7 ends with the same
in/out ablation that cut structural v1.

### As agent tools

The same traversal, exposed so the agent expands a location **it** chose:
`get_callers(entity)`, `get_callees(entity)`, `list_dependencies(path)`.

## Fusion

**Weighted Reciprocal Rank Fusion**: `score = Σ w_leg / (k + rank)`, **`k=10`**, weights
**lexical 1 / semantic 2**, each leg contributing exactly `limit` candidates. RRF uses each
hit's *rank position*, never raw scores — `ts_rank`, cosine similarity, and hop distance
never need a common scale. The weight is a vote size: equal votes let the weaker leg out-vote
the stronger one's correct pick; 2× lets semantic set the order while lexical still adds the
hits semantic misses. The paper's `k=60` and a `2×limit` over-fetch were measured and
rejected: on 20-deep lists they let "in both legs at rank 40" outscore "rank 2 in one leg"
(`CONCEPTS.md` A22). Lexical is passed first
because the metadata merge is first-wins, so its honest `match_line` survives. The per-file
cap is re-applied after fusion (two legs at their cap can still put one file in 4 slots).

**No reranker in V1.** Retrieve-then-rerank is the standard two-stage shape and is deferred,
not rejected: RRF plus the agent's own ability to discard bad tool results covers it until the
eval shows otherwise.

## Operational constraints

- **Route before you embed.** A bare-identifier query (`^[A-Za-z_]\w*$`) resolves through
  lexical in ~10 ms; an embedding call first adds ~100 ms for a worse answer.
- **Keep the prompt prefix stable.** System prompt → tool definitions → repo map, byte-identical
  across every question about a repo — never a timestamp or the user's question early in it.
  One changed byte invalidates every cached token after it.
- **Stream tool-call status, not just tokens.** *"searching `TokenService`… reading
  `auth/tokens.py`…"* is the difference between alive and hung.

## How the agent uses it (M8)

Retrievers are exposed as **tools**: `code_search(query)`, `read_file(path, start?, end?)`,
`list_dependencies(path)`, `get_callers(entity)`, `get_callees(entity)`.

The loop is a **hand-rolled `StateGraph`**, not `create_react_agent`:

```
START ──> call_model ──> should_continue? ──> tools ──┐
                              │                       │
                              └──> END       <────────┘
```

We define the state (messages + accumulated citations), `call_model` (bind tools, invoke the
chat model from `core/ai/chat.py`), the `ToolNode`, and `should_continue`. LangGraph is only
the executor. There is no single-shot RAG version — once retrievers are tools, the loop is a
handful of lines on top.

**Repo map.** The agent's weak moment is the *first* tool call, guessing a search term with no
sense of the codebase. Fix: a names-only table of contents (each file with its top-level
symbols) in the stable prompt prefix, ranked by **PageRank over the import graph** so central
files survive trimming. Built from `code_entity` + `dependency_edge`, no AI calls — aider's
idea. Raw centrality over-ranks generic utilities; acceptable, the map only orients.

**Citations come from tool-result metadata, never from the model.** The retriever already
knows file and line range; asking the model where it found something invites drift.

**Hallucination guards, both without an extra model call.** (1) *Retrieval grading*
(CRAG-style) right after each tool call: junk or empty results trigger a re-search instead of
reaching `call_model`. (2) *Citation verification* before `END`: every `file:line` in the
draft is checked against the hits collected this turn; unbacked citations are dropped. This is
structural — it catches *fabricated* citations, not a real citation that doesn't support its
sentence. LLM-judge / NLI faithfulness scoring is deferred until the eval shows these two
aren't enough.

## Build order & measurement

Legs ship one at a time, and **each ends with a measured `recall@k` delta** against a pinned
baseline — if several fused retrievers return junk you can't tell which is at fault.

| Leg | Ships in | Cost to redo |
|---|---|---|
| Lexical | **M5** ✅ | trivial — one migration |
| ~~Structural v1 (trigram)~~ | M4 → removed after M5 measurement | trivial |
| Semantic | **M6** ✅ | re-embed the corpus |
| Graph | **M7** | re-parse + re-resolve; no API cost |

**M6 measured (2026-09-04, OpenAI `text-embedding-3-small`, all 46 questions, line-level):**

| legs | @5 | @20 | symbol | keyword | conceptual | docs |
|---|---|---|---|---|---|---|
| lexical (baseline) | 0.72 | 0.78 | 0.93/0.93 | 0.86/0.86 | 0.36/0.50 | 0.75/1.00 |
| semantic only | **0.85** | **0.93** | 1.00/1.00 | 0.93/0.93 | **0.64/0.86** | 0.75/1.00 |
| fused, `2×limit`, k=60, equal weights (original) | 0.72 | 0.87 | 1.00/1.00 | 0.79/1.00 | 0.29/0.57 | 1.00/1.00 |
| fused, `limit`, k=10, equal weights | 0.78 | 0.89 | 1.00/1.00 | 0.86/1.00 | 0.43/0.64 | 1.00/1.00 |
| **fused, `limit`, k=10, semantic 2× (shipped)** | **0.85** | 0.91 | 1.00/1.00 | 0.93/**1.00** | 0.57/0.71 | **1.00/1.00** |

Three findings. **The semantic leg clears its bar easily** — conceptual `@5` 0.36 → 0.64,
+0.13 overall, exactly the class it exists for. **Fusion needed two fixes before it earned
its place.** The original constants (`k=60`, over-fetch) fused to 0.72 — no better than
lexical — because on 20-deep lists "appears in both legs" outscored "rank 2 in one leg"
(`CONCEPTS.md` A22); `fetch = limit`, `k=10` recovered to 0.78. Still below semantic alone,
because with equal votes lexical's junk out-voted semantic's correct pick on conceptual
questions. **Weighted RRF (semantic 2×) is where fusion beats both legs**: semantic's `@5`
plus lexical's coverage on the buckets semantic misses (keyword `@20` 0.93 → 1.00, docs
0.75 → 1.00). At 3× and above the result is identical to semantic-only — lexical has been
weighted out. Caveat: this eval feeds raw English, which favours semantic; the agent will
send identifier-shaped queries, where the two legs tie.

## Evaluation

Retrieval quality is the whole game, and "the answers feel good" is not a measurement.

46 questions across 3 repos pinned to immutable commit SHAs (so answer-key lines never drift),
each recording the paths and line ranges that contain the answer:

```yaml
- q: "Where is the session cookie signed?"
  repo: noetra
  kind: conceptual
  answers:
    - { path: "app/session.py", lines: [40, 68] }
```

`eval/run.py` reports **recall@5** and **recall@20** — recall rather than precision, because
the agent can discard a bad hit but cannot recover a correct file retrieval never surfaced.
**Line-level next to file-level** ("right file, wrong lines" is its own failure mode), broken
down by question kind (`symbol` / `keyword` / `conceptual`) and by repo. A fourth kind,
`docs`, is a guard-rail: four questions whose answers genuinely live in prose, so the
non-source penalty can't be tuned into burying documentation while "scoring better".

Day-to-day runs use `--repos noetra` (the default); `--repos all` brings `requests` and `zod`
back. `--legs lexical` / `--legs lexical,semantic` ablate a leg in or out.

**Baselines (all 46, line-level): lexical-only recall@5 0.72 / @20 0.78; semantic-only
0.85 / 0.93; fused (shipped, semantic 2×) 0.85 / 0.91.** Quote `@5`; at these repo sizes
`@20` is a soft bar. The eval feeds **raw English** to `search()`,
which the agent never will — it reformulates first — so the conceptual bucket is realistic for
the search UI and pessimistic for the agent. M8 extends `run.py` to score agent-mediated
retrieval alongside it.

## Build note

`core/retrieval` owns all of this — both `/search` and the agent tools call into it, and no
retrieval logic lives anywhere else. `core/ai` is the only module that imports any AI
provider's SDK.
