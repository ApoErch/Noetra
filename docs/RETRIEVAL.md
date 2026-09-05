# Retrieval (the core)

Noetra uses **agentic RAG**: two fused retrieval legs (lexical + semantic) that answer
different kinds of question, graph tools that walk from a location to whatever is connected
to it, and an agent that decides which to use, in what order, and how many times. The agent
is only as good as what this returns.

**Agentic** means retrieval is not a fixed pre-LLM step — the model calls it mid-reasoning and
iterates. An exact identifier resolves in **one** `code_search` and never touches the vector
index. A vague conceptual question may search, follow the top hit via `find_references`, then
search again with better terms. That per-query branching is what separates it from
RAG-with-extra-steps.

## The legs

| Leg | Answers | Status |
|---|---|---|
| **Lexical** — Postgres full-text over chunk `embed_text` | identifiers, error strings, config keys | shipped (M5) |
| **Semantic** — pgvector cosine over the same chunks | questions whose words appear *nowhere* in the code | shipped (M6), measured |
| **Graph** — call + import edges, walked one hop at a time | "what does this call?", "who calls this?", "what does this import?" | shipped (M8), measured — agent tools, **not a fused leg** |

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

**Graph.** Takes **locations**, returns **connected locations**. That's *reachability*, not
*relevance* — which is why it is exposed as tools the agent calls and never fused. Details
below.

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

## The graph: tools, not a leg (M8)

`dependency_edge` is the **file-level import graph** (M4, already built). `reference_edge`
adds the **call graph**: which entity calls which, resolved **by name** against the symbol
table. No type inference: the standard "poor man's call graph" (ctags, aider), roughly right
and enough to orient an agent that then reads the real file. Each edge carries a
**confidence** from its resolution tier — same file (0.9) → a file this one imports (0.85)
→ unique repo-wide name (0.7) → ambiguous, one edge per candidate (0.3) — so callers can
filter ambiguity out (default `≥ 0.5`). This is the cascade Codebase-Memory and LARGER use.

**Why it is not an RRF leg.** The original M7 plan fused graph neighbours in as a third
ranked list, seeded from the lexical + semantic top hits. Dropped on 2026-09-04 after
checking what the field does. The argument:

- RRF combines **independent estimates of query relevance**. Hop distance from a seed is a
  property of the *seed*, not the *query* — feeding it to RRF labels "adjacent to something
  relevant" as "relevant".
- A seeded leg is not independent: it can only ever amplify what the other two legs already
  voted for. The coupling cost the old plan "accepted deliberately" was the whole leg.
- Whether a neighbour matters depends on the *question*. "Where is X defined" needs zero
  hops; "how does auth flow work" needs several. A fixed leg can't know which; an agent can.

**What the field does — nobody fuses the graph.** LARGER (2026), the closest published
design to the old plan, attaches confidence-filtered 1-hop neighbours *to the anchoring hit
in the same tool observation* — expansion was its largest single gain (MuLocBench Acc@5
55.7 → 48.2 without it), but it is a sidecar on a hit, never a rank vote. RepoGraph: 1-hop
best, **2-hop worst** (29.7 → 26.0 resolve rate) — "noise dominates". LocAgent exposes
`TraverseGraph` as a tool: removing it costs −4 pts; removing keyword search costs −13 —
search is the workhorse, the graph is real but second-order. Augment's context engine keeps
"a graph index of definitions and call edges for **structural reachability**" beside its
vector and BM25 indices. Sourcegraph and Greptile use the graph as "find references, pull
in that context" after search. Aider uses it only to rank the repo map. GraphRAG-Bench
(ICLR'26) finds graphs win on multi-hop and *lose* on simple lookups because expansion adds
noise; CodeCompass calls it the *navigation paradox* — rigid graph structure degrades agents.
Pattern name for what we keep: **graph-augmented agentic retrieval**, not graph-fused search.

**The tools.** `list_dependencies(path, direction)` (M7 — free, `dependency_edge` exists)
and **`find_references(symbol, direction)`** (M8) — one tool with a `callers`/`callees`
parameter rather than two, mirroring `list_dependencies`' shape and matching how LocAgent
(`TraverseGraph`) and ARISE (`traverse_relations`) collapse direction (`CONCEPTS.md` B25).
One hop per call; the agent hops again if it wants to. Results are `RetrievalHit`s: an entity
maps back to **its chunk** via `chunk.entity_id`, so the citation is the same chunk-aligned
range every other tool emits, with `match_line` = the call site.

**Why it beats search at this, concretely.** `code_search` caps at `MAX_CHUNKS_PER_FILE = 2`
(`core/retrieval/types.py`), applied per leg *and* again after fusion. So it can never return
more than two locations from one file — "list every call site in this module" is out of reach
by construction, not by ranking. `find_references` returns the complete set, and knows *which*
`request` you meant when a repo defines the name twice.

## Fusion

**Weighted Reciprocal Rank Fusion**: `score = Σ w_leg / (k + rank)`, **`k=10`**, weights
**lexical 1 / semantic 2**, each leg contributing exactly `limit` candidates. Two legs by
design — the graph never enters it (above). RRF uses each hit's *rank position*, never raw
scores — `ts_rank` and cosine similarity never need a common scale. The weight is a vote size: equal votes let the weaker leg out-vote
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

## How the agent uses it (M7 — shipped 2026-09-05)

Retrievers are exposed as **tools** the model calls in a loop (`core/agent/tools.py`):

| tool | returns | notes |
|---|---|---|
| `code_search(query)` | ≤10 lines of `path:start-end · snippet [lexical,semantic]` | the fused `search()`; 10 not 20 — the agent can search again, and every junk line is context it carries |
| `read_file(path, start?, end?)` | numbered `NN\| code` lines, **≤200 per call** | the read range becomes citable; unknown path → "did you mean" from the repo's paths |
| `list_dependencies(path, direction)` | paths | `imports` or `imported_by`, over `dependency_edge` |
| `find_references(symbol, direction)` | ≤30 lines of `path:start-end · caller — calls X at line N` | `callers` or `callees`, over `reference_edge`; the complete set, not a ranked sample |

Every tool result line carries `path:start-end`
— **that** is where citations come from; the model is told to cite only ranges it saw.

Two real turns from the eval:

```
decrypt_token
  1. code_search("decrypt_token")            → 10 hits, top: backend/core/security.py:17-24
  2. read_file("backend/core/security.py", 17, 24)
  3. answer, citing [backend/core/security.py:22-24]        2 calls · 5k tokens · 5 s

How does the app stop two indexing jobs running for the same repository at once?
  1. code_search("prevent duplicate indexing jobs same repository")
  2. read_file("backend/core/redis_client.py", 1, 17)
  3. read_file("backend/api/repos.py", 49, 88)
  4. answer, citing redis_client.py:10-12, repos.py:60-70    3 calls · 9k tokens · 5 s
```

An identifier question ends after one search. An off-topic question ("write me a poem")
ends with zero tool calls, because the system prompt says to decline and redirect — that is
the whole "router". The graph tool is called only once the agent *has* a location
and asks what is connected to it, never at step 1.

The loop is a **hand-rolled `StateGraph`** (`core/agent/graph.py`), not `create_react_agent`:

```
START ──> call_model ──(tool calls?)──> tools ──> call_model …
              │
              └──(answer)──> verify_citations ──> END
```

- `call_model` binds the tools to the chat model from `core/ai/chat.py`. System prompt →
  tool schemas → repo map come first and are byte-identical per repo, so OpenAI's automatic
  prompt cache hits every turn. Once the **tool budget** (`AGENT_TOOL_BUDGET`, default 8) is
  spent, tools stay declared but `tool_choice="none"` forces an answer.
- `tools` is our own node, not the prebuilt `ToolNode`: each result is appended as a
  `ToolMessage`, its ranges go into `state.hits`, a UI trace line is recorded, and the
  **no-progress guard** blocks an identical repeat call with a hint instead of running it.
  Parallel tool calls in one model message run together.
- `verify_citations` (`core/agent/citations.py`): every `[path:a-b]` in the answer must name
  a path retrieved this turn with an overlapping range; anything else is demoted to plain
  text. Zero model calls. Across 64 eval answers it stripped **nothing** — the model never
  fabricated a location — but it is what makes that claim checkable.

`END` ends a *turn*, not the conversation: the next message re-invokes the graph with the
stored history prepended (see Memory).

**Repo map** (`core/agent/repo_map.py`). Names-only table of contents — each parsed file with
its top-level classes/functions — ordered by **PageRank over the import graph** (hand-rolled
power iteration, ~15 lines) and cut at `AGENT_REPO_MAP_TOKENS` (1,500). Cached per repo until
re-index. Measured: on gpt-5.4-mini it made no difference to recall, but *without* it the
model answered one keyword question from memory with **zero tool calls** and no citation.
The map's job turned out to be keeping the model in "look it up" mode, not just orienting
the first search.

**What was planned and dropped.** The M7 plan had a *router node* (on/off-topic classifier
before the loop) and a *CRAG-style grader node* after each tool call. Both were cut before
building, on evidence: in a tool-calling loop the model already sees each result and decides
whether to search again, so a grader is a second opinion on a decision it makes anyway; and
the off-topic branch ends in the same model call with zero tools. A 2026 repo-QA study
(arXiv 2608.01507, 4 models × 15 repos) found a plain search+read loop beat an
orchestrator/sub-agent design 65 % → 46 % at half the cost, and that extra tool calls
correlated *negatively* with correctness. The mechanical guards (empty-result steering,
no-progress, budget, citation verifier) cover what the nodes would have. Revisit triggers
are recorded in `CONCEPTS.md` B21.

**Memory.** Our own `chat_conversation` / `chat_message` tables, not a LangGraph
checkpointer. Each turn replays only the last 12 *user/assistant* messages — never old tool
results. A checkpointer would replay every tool output ever produced (turn 3 carries turns
1–2's file reads: ~12k tokens vs ~3k), the UI would read history out of opaque state blobs,
and its real strengths (resume mid-step, human-in-the-loop, time travel) aren't needed for a
few-second chat turn. This is Anthropic's "tool result clearing" done structurally.

**Model.** `gpt-5.4-mini` (default). Measured against `gpt-4.1-mini` on the agent eval
(46 questions): cited 0.91 vs 0.59, fewer tool calls (3.3 vs 3.8), faster (4 s vs 7 s),
~1 ¢ vs ~0.5 ¢ per question. Most of 4.1-mini's gap is citation-format compliance rather
than retrieval — see the caveats under the measurement table.

## Build order & measurement

Legs ship one at a time, and **each ends with a measured `recall@k` delta** against a pinned
baseline — if several fused retrievers return junk you can't tell which is at fault. The
graph is not a fused leg, so it is measured where it is used: on the M7 agent eval, tools on
vs. off, plus an edge-quality eval of the resolved call graph itself.

| Leg | Ships in | Cost to redo |
|---|---|---|
| Lexical | **M5** ✅ | trivial — one migration |
| ~~Structural v1 (trigram)~~ | M4 → removed after M5 measurement | trivial |
| Semantic | **M6** ✅ | re-embed the corpus |
| Agent (M7) ✅ | **M7** — measured 2026-09-05 | prompt/tool edits; re-run `eval.agent` |
| Graph (agent tools, not fused) ✅ | **M8** — measured 2026-09-06 | re-parse + re-resolve; no API cost |

**M7 agent eval (2026-09-05, all 46 questions, `eval.agent --repos all`):** *retrieved* = a
tool returned a range overlapping the answer key; *cited* = a citation that survived the
verifier overlaps it — i.e. the user got a clickable link to the right code.

| run | retrieved | cited | calls/q | tokens/q | stripped |
|---|---|---|---|---|---|
| **gpt-5.4-mini + repo map (shipped)** | **0.93** | **0.91** | 3.3 | 11.6k | 0 |
| gpt-5.4-mini, no map | 0.91 | 0.87 | 3.3 | 6.7k | 0 |
| gpt-4.1-mini + repo map | 0.87 | 0.59 † | 3.8 | 12.9k | 0 |

(The first 16 noetra questions alone: 5.4-mini 16/16 cited, 4.1-mini 13/16.)

**Read the numbers with these four caveats — all measured, none fixed yet:**

1. **The keys are one location each, hand-written, unreviewed** (`CONCEPTS.md` A26). Three
   of the four 5.4-mini "misses" are correct answers at a location the key doesn't list
   (zod's `.describe()`/`.meta()` in `classic/schemas.ts` vs. the key's `registries.ts`;
   `extend()`'s spread-copy vs. two helper functions 200 lines up; a different section of
   the same `metadata.mdx`). Only "Transfer-Encoding chunked" is a real miss — docs and
   tests cited instead of `prepare_body`. Realistic score ≈ 45/46. Fix: add those
   locations as extra `answers` entries and re-score offline (`eval/out/*.jsonl` keeps the
   answers).
2. **† 4.1-mini's 0.59 is mostly citation *format*, not retrieval.** It found the code and
   wrote "lines 90-99" or "[from src/requests/models.py:1144-1171]", which the strict
   `[path:a-b]` verifier rejects. That is still a product failure (no clickable link) but it
   overstates the model gap. Option: accept bare `path:a-b` in the verifier and the UI.
3. **`tokens/q` counts the repo map once per model call**, because the whole prompt is
   re-sent every call (3.3 tool calls ≈ 4.3 model calls × 1.5k). Those tokens are the
   byte-identical prefix OpenAI's prompt cache serves at a fraction of the price and with no
   re-processing — which is why wall-clock was equal with and without the map (4.0 s vs
   3.8 s). The eval does not yet separate cached from uncached input; until it does, the
   token column overstates the map's cost. Next step: report cents/question from
   `usage_metadata.input_token_details`, and try an 800-token map.
4. **The map's recall gain (+0.04 = 2 questions) is inside the noise at n=46.** It is kept
   for the behavioural finding in `CONCEPTS.md` A25: without it, the model answered a code
   question from its own memory with zero tool calls. That is the one failure this product
   must never show, and the map is the cheapest known fix.

**M8 graph tool (2026-09-06, `eval.agent --kinds graph --repos all`, n=12).** The 46
questions above could not measure this: they were saturated at 0.91 with ~1 question of real
headroom, and none of them asks for a *set* of locations (`CONCEPTS.md` A28). A fifth question
kind, `graph`, was written first — enumeration questions whose keys list **every** correct
location, scored on **coverage** (`ccov`) rather than a boolean, because naming two of five
call sites is a wrong answer the boolean records as a win (A29).

| run | retrieved | cited | rcov | **ccov** | calls/q | tokens/q |
|---|---|---|---|---|---|---|
| 3 tools (`find_references` off) | 0.92 | 0.75 | 0.82 | **0.63** | 5.8 | 15.7k |
| **4 tools (shipped)** | **1.00** | **0.92** | **0.91** | **0.84** | **4.2** | **10.9k** |

**+0.21 coverage while cutting tool calls 28 % and tokens 30 %** — better answers *and*
cheaper, which is the efficiency result Codebase-Memory reports for graph tooling. Per repo,
`ccov`: noetra 0.72 → 1.00, zod 0.33 → 0.83. The mechanism, on one question — *"list every
call site of `_get_owned_repository`"*: without the tool, 3 calls, retrieved but never cited
(the per-file cap returns 2 of 5); with it, **1 call**, all five cited.

On the original 46 the tool changes nothing (`cited` 0.91 → 0.89, one question of variance) —
correct, not disappointing: it adds a capability rather than improving an existing one, which
is why it needed its own bucket (`CONCEPTS.md` B27). Read the headline delta only; sub-rows
like `requests` (n=4) are noise.

**Also measured and rejected:** feeding call edges into the repo map's PageRank (aider's
design). Graph `ccov` 0.84 → 0.72, the other 46 `cited` 0.89 → 0.87 — reverted. Import edges
count **breadth** (how many distinct files depend on this), call edges count **volume** (how
many times it is invoked), and orientation needs breadth: a test helper called often from few
files was promoted into the token-capped top ten and evicted a real source file
(`CONCEPTS.md` A31).

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
which the agent never will — it reformulates first — so the conceptual bucket is pessimistic
for the agent. `eval/agent.py` scores the agent itself (retrieved / cited / cost per question,
checkpointed per question so an interrupted run resumes). A fifth kind, `graph`, scores the
M8 tool — see below.

## Build note

`core/retrieval` owns all of this — both `/search` and the agent tools call into it, and no
retrieval logic lives anywhere else. `core/ai` is the only module that imports any AI
provider's SDK.
