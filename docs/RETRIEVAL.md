# Retrieval (the core)

Pure vector search over code gives weak, hand-wavy answers. Noetra uses **agentic RAG**:
three retrieval legs that answer different kinds of question, and an agent that decides
which to use, in what order, and how many times. The agent is only as good as what this
returns.

## What "agentic RAG" means here

The distinguishing property is **not** "an LLM plus retrieval tools." It's that retrieval is
no longer a fixed pre-LLM step — the model invokes it mid-reasoning and iterates. Noetra's
agent picks its strategy **per query**:

- An exact identifier or error string may resolve in **one** `code_search` and never touch
  the vector index.
- A vague conceptual question may search, expand the top hit via `get_callers` for connected
  context, then search again with reformulated terms if the first pass came back thin.

That adaptive branching is what separates agentic RAG from RAG-with-extra-steps. It's not
"one embedding lookup then answer," and not "pure grep-and-read with no vector index."

## Decision record: two different things have been called "structural"

One was cut and the other is being built, so conflating them makes the history look
contradictory.

**Structural v1 — trigram symbol-*name* lookup. Built, measured, cut.** M5 shipped fuzzy
`pg_trgm` lookup over `code_entity.name`. The eval ablation (46 questions, in vs. out) showed
pulling it dropped `symbol` recall@5 from 1.00 → 0.93 and overall from 0.76 → 0.72. Real, but
every point came from one case: identifiers the Postgres text-search tokenizer mangles
(`$ZodRegistry`). Everything else it found, lexical already found — because chunking is
AST-aware, so a function's definition line is usually the top lexical hit for its own name.
Removed rather than kept for a single-question win. Same rule as always — *no retriever joins
the fusion without a `recall@k` movement that justifies it* — applied in the other direction.
The rule cuts a retriever as readily as it admits one, once you have the number.

**Structural v2 — graph *traversal*. Never built at any layer; building it in M7.** Call and
import edges: *"who calls `authenticate`?"* Different data (`reference_edge`, not
`code_entity.name`), different input (a **location**, not a query). The v1 cut says nothing
about it.

## The three legs

**1. Lexical** — exact/keyword. *Shipped (M5).*
Postgres full-text over **chunk** `embed_text`, so file path and enclosing class are in the
index for free. Two passes: strict `websearch_to_tsquery` (which AND-joins bare terms), then
an OR-relaxed rewrite backfilling unused slots — strict hits keep their positions, so
relaxation cannot cost precision. A per-file cap of 2 chunks stops one verbose file taking
every slot; a 0.3 `ts_rank` penalty where `language IS NULL` stops prose *about* code
outranking the code. Answers identifiers, error messages, config keys.

**2. Semantic** — meaning, via embeddings. *M6.*
pgvector cosine similarity over the same AST-aware chunks. The only leg that earns its keep
when the user's words appear **nowhere** in the codebase — a real and important class, but a
**minority** of code questions. Most of the time the identifier you want is literally in the
file, which is why lexical carries more weight than vector-first intuition suggests.
*"how does authentication work?"* → `core/security.py`, which never says "authentication."

**3. Graph** — connection, via traversal. *M7.*
Takes **locations**, returns **connected locations**. Used two ways: a third RRF leg seeded
from the other two, and direct agent tools. See below.

## The embedding model

**`gemini-embedding-001`**, free tier, at **1536 dimensions**.

- Default output is **3072**, Matryoshka-truncatable to 1536/768. We take 1536 because
  **pgvector's HNSW caps the `vector` type at 2000 dims** — 3072 would force `halfvec`.
- **Only 3072 is pre-normalized.** At 1536 we L2-normalize client-side or cosine distance is
  silently wrong — degraded recall with no error to catch it.
- **Input caps at 2048 tokens** (~8 KB). Some leaf-entity chunks exceed this, so `core/ai`
  truncates before sending.
- **`task_type` is asymmetric.** Index with `RETRIEVAL_DOCUMENT`; query with
  **`CODE_RETRIEVAL_QUERY`** — Google's purpose-built "natural language → code block" mode,
  exactly this product's query shape. Using one task type for both sides is a quiet mistake.

## Chunking rule (non-negotiable)

Chunk by **AST node** via Tree-sitter — never fixed token count. A chunk that splits a
function in half poisons retrieval. One chunk per **leaf entity**, plus **gap chunks**
covering everything between, so no line is unsearchable.

**Embed the chunk with its context prefixed:**

```
src/auth/tokens.py › class TokenService › def refresh(self, token: str) -> Token
<the actual chunk source>
```

A bare body is ambiguous — `def refresh(self, token)` could be a cache, a session, or an
OAuth token. The prefix tells the model which, so the vector lands near "authentication"
instead of somewhere generic. This is **contextual retrieval**, the cheapest large recall
gain here: one f-string, no extra API calls, no schema change.

Stored as `chunk.embed_text`; raw `chunk.content` goes back to the model. The lexical
`tsvector` generates over `embed_text` too — contextual retrieval on the lexical leg for free.

## The graph leg

`dependency_edge` is a **file-level import graph**. `reference_edge` (M7) adds the **call
graph**: which entity calls which. Resolution is **name-based** — match callee names against
the symbol table, preferring same-file then imported-file candidates, emitting all candidates
when genuinely ambiguous. No type inference. This is the standard "poor man's call graph"
(ctags, aider): roughly right, cheap, enough to orient an agent that then reads the real file.

### As a fusion leg (seeded expansion)

```
query ──┬── lexical  ──> top N ──┐
        └── semantic ──> top N ──┴──> resolve chunks to entity_ids
                                             │
                                    expand(seeds, max_hops=2)
                                    over reference_edge ∪ dependency_edge
                                             │
                                    hop-ranked (1-hop above 2-hop, tie-broken
                                    by how many distinct seeds reached it)
                                             │
  RRF([lexical, semantic, graph]) ───────────┘  ──> final ranked hits
```

**Seed resolution.** Hits are *chunks*; the graph is over *entities*. `chunk.entity_id`
resolves leaf-entity chunks in one join. Gap chunks (docs, config, code between functions)
have it null and don't seed — they still reach the result via lexical/semantic.

**Key alignment — the trap that silently breaks this.** `fusion.py` dedupes on
`(file_id, start_line, end_line)`. If the graph leg emits *entity* ranges while the others
emit *chunk* ranges, keys never collide, RRF can never fuse anything, and it degenerates into
concatenation **that still looks like it's working**. The graph leg must map each expanded
entity back to its chunk before emitting a `RetrievalHit`.

**The cost, stated honestly.** Seeding from the other legs' top hits **couples the
retrievers**: a wrong seed is *reinforced* by its neighbours instead of cancelled out by an
independent leg — the opposite of what RRF over independent retrievers buys. A deliberate
trade-off, which is why M7 ends with the same in/out ablation that cut structural v1.

### As agent tools

The same traversal, exposed so the agent expands a location **it** chose:
`get_callers(entity)`, `get_callees(entity)`, `list_dependencies(path)`.

## Fusion

**Reciprocal Rank Fusion**: `score = Σ 1/(k + rank)`, `k=60`. RRF uses each hit's *rank
position*, never raw scores — so lexical's `ts_rank`, semantic's cosine similarity, and the
graph's hop distance never need a common scale. Returns one deduplicated ranked list where
each result knows its file, line range, and source retriever(s).

**No reranker in V1.** This doc previously specified one (fuse wide, cut narrow). It isn't
being built: there is no free Gemini reranker, and LLM-as-reranker would spend the ~10 RPM
chat quota on every search — the same quota the agent loop needs. RRF plus the agent's own
ability to discard bad tool results covers it. A recorded decision, not an oversight.

## Operational constraints

These sit in the user's perceived response time or in the free-tier budget, so they're design
constraints, not later optimizations.

- **Route before you embed.** A bare-identifier query (`^[A-Za-z_]\w*$`) resolves through
  lexical in ~10 ms; an embedding call first adds ~100 ms for a worse answer. Skip it.
- **Keep the prompt prefix stable.** System prompt → tool definitions → repo map, byte-identical
  across every question about a repo. Never interpolate a timestamp, request ID, or the user's
  question early in it — one changed byte invalidates every cached token after it.
- **Stream tool-call status, not just tokens.** A 10 s loop showing *"searching
  `TokenService`… reading `auth/tokens.py`…"* is the difference between alive and hung.
- **Free-tier limits shape the architecture.** `gemini-2.5-flash` is ~10 RPM / 250 RPD and an
  agent turn is 3–6 calls, so a 46-question agent eval is ~200 calls — most of a day. It
  **must** checkpoint per question and resume. Likewise `chunk.embedding` is **nullable** and
  the embedding stage selects `WHERE embedding IS NULL`, so a 429 resumes instead of
  restarting the repo.

## How the agent uses it (LangGraph)

Retrievers are exposed as **tools**, not one pre-baked context blob: `code_search(query)`,
`read_file(path, start?, end?)`, `list_dependencies(path)`, `get_callers(entity)`,
`get_callees(entity)`.

The loop is a **hand-rolled `StateGraph`**, not `create_react_agent`:

```
START ──> call_model ──> should_continue? ──> tools ──┐
                              │                       │
                              └──> END       <────────┘
```

We define the state (messages + accumulated citations), `call_model` (bind tools, invoke
`gemini-2.5-flash`), the `ToolNode`, and `should_continue` (tool calls present → loop; none →
finish). LangGraph supplies only the runtime — it's a state-machine executor, not the agent.
Writing it out is what makes the citation-collection node and repo-map priming natural.

The agent iterates like a developer: search → read → realize it needs a caller →
`get_callers` → answer.

### Repo map (agent orientation)

Agentic search has one weak moment: the *first* tool call, where the agent must guess a search
term with no sense of the codebase's shape — worst on vague questions. A wrong first guess
means wandering or a miss.

Fix: a **repo map** — a compressed, names-only table of contents (each file with its top-level
symbols, no bodies) in the stable prompt prefix, so it orients from a floor plan instead of
guessing blind. Same idea aider uses.

Built entirely from data M4 already persisted, **no AI calls**: `code_entity` for names,
`dependency_edge` for the import graph. Too many symbols to show them all, so rank by
**PageRank over the import graph** — a file many files import is probably central; leaf files
nobody imports get trimmed. Caveat: raw centrality over-ranks generic utilities (a `utils.py`
everyone imports floats up), which PageRank dampens but doesn't eliminate. Acceptable, because
the map only needs to *orient* the agent, which then verifies by reading real files.

### Citations

**Citations come from tool-result metadata, never from the model.** The retriever already
knows the file and line range; carry it through and render it. Asking the model where it found
something invites drift, or citing a file it reasoned about but never opened. That's the
difference between a citation the user trusts and one they stop clicking.

There is no single-shot RAG version. It's tempting to think "one retrieval call, no loop" is
the simpler first step, but once retrievers are exposed as tools the loop is a handful of lines
on top — and the single-shot version would be thrown away immediately. Ship the agent.

## Build order & measurement

Legs get built one at a time, and **each ends with a measured `recall@k` delta**.

| Leg | Ships in | Cost to redo |
|---|---|---|
| Lexical | **M5** ✅ | trivial — one migration |
| ~~Structural v1 (trigram)~~ | M4 → **removed after M5 measurement** | trivial |
| Semantic (embeddings) | **M6** | **re-embed the entire corpus** |
| Graph (call + import edges) | **M7** | re-parse + re-resolve; no API cost |

**Semantic is no longer conditional.** It was gated on "only if the eval proves lexical is
failing" because embeddings were expensive to build *and* to redo. At $0 the cost half of that
argument is gone, and agentic RAG needs a semantic leg for the conceptual class. The eval stops
being a *gate* and goes back to being a *scoreboard* — every leg still shows its delta.

The discipline stays: if several fused retrievers return junk you can't tell which is at fault,
so each leg lands against a clean pinned baseline. That's what made the structural ablation
possible.

## Evaluation

Retrieval quality is the whole game, and "the answers feel good" is not a measurement.

46 questions across 3 repos pinned to immutable commit SHAs (so answer-key lines never drift),
each recording the paths and line ranges that actually contain the answer:

```yaml
- q: "Where is the session cookie signed?"
  repo: noetra
  kind: conceptual
  answers:
    - { path: "app/session.py", lines: [40, 68] }
```

`eval/run.py` reports **recall@5** and **recall@20** — how many questions had a correct
location in the top 5 / top 20. Recall rather than precision, because the agent can discard a
bad hit but cannot recover a correct file retrieval never surfaced.

Two things worth keeping:

- **Line-level next to file-level.** "Right file, wrong lines" is a distinct failure mode from
  "never found it" and needs its own number.
- **A `docs` question kind as a guard-rail.** The non-source rank penalty was tuned against an
  answer set that was 100% source files, so it could only ever look good — cranking it to 0.001
  would have "scored better" while making prose unreachable. Four questions whose answers
  genuinely live in prose keep that honest.

**Current baseline (lexical-only): line-level recall@5 0.72, recall@20 0.78.** Quote `@5`; at
these repo sizes `@20` is a soft bar (20 candidates ≈ 15–25% of a 60–130-file repo).

The eval feeds **raw English** to `search()`, which the agent never will — it reformulates
first. So the conceptual bucket is realistic for the search UI and pessimistic for the agent
path. M8 extends `run.py` to score agent-mediated retrieval alongside it.

## Build note

`core/retrieval` owns all of this — both `/search` and the agent tools call into it, and no
retrieval logic lives anywhere else. `core/ai` is the only module that imports the Gemini SDK.
