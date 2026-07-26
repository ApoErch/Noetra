# Retrieval (the core)

Pure vector search over code gives weak, hand-wavy answers. Noetra uses **hybrid
retrieval**: retrievers that answer different kinds of question, fused into one ranked
result set. This is the most important part of the product — the agent is only as good
as what this returns.

## Decision record: structural retrieval was built, measured, and cut

M5 originally shipped **three** retrievers: lexical, structural (trigram lookup over
`code_entity`, `structural_search()`), and semantic (conditional, M7). Once the eval
harness existed, we ran the ablation the harness exists to enable: ran the 46-question
eval with and without structural in the fusion. Result — pulling structural out dropped
`symbol`-bucket recall@5 from 1.00 to 0.93, and overall recall@5 from 0.76 to 0.72. Real,
but concentrated in one case: identifiers containing characters the Postgres text-search
tokenizer mangles (`$ZodRegistry`) or garbles the boundaries of. Everything else
structural found, lexical already found on its own — because chunking is AST-aware, so a
function's own definition line is usually the highest-signal chunk lexical returns for its
name anyway.

Given that, structural retrieval (`core/retrieval/structural.py`) was removed rather than
kept for a single-question, single-repo win: one fewer query per search, one fewer thing
to keep correct, no measured loss worth the complexity. `search()` is lexical-only until
M7. This is the same "no retriever joins the fusion without a `recall@k` movement that
justifies it" rule applied in the other direction — the rule cuts a retriever exactly as
readily as it admits one, once you have the number.

**What structural retrieval was not**, despite the name: it never did call-graph traversal
("who calls `authenticate`?") — that data was never extracted at all, at any layer. See
"Deferred: call-graph extraction" below.

## The two retrievers

**1. Lexical** — exact/keyword matching.
Postgres full-text (`tsvector`) over file content and identifiers.
Answers: exact string/identifier lookups, error messages, config keys.
*"find every place that references `STRIPE_SECRET`."*

**2. Semantic** — meaning, via embeddings.
pgvector similarity over **AST-aware chunks** (chunked by function/class, never fixed
token windows). OpenAI `text-embedding-3-small` — 1536 dimensions, cheap, good enough for
code; `-large` (3072) is the upgrade path if evals justify it. OpenAI's `dimensions`
parameter can truncate the vector later, so the index can shrink without changing models.
Answers: conceptual, fuzzy questions where the words don't match the code.
*"how does authentication work?"*

This is the only retriever that earns its keep on questions where the user's words appear
nowhere in the codebase — which is a real and important class, but a **minority** of code
questions. Most of the time the identifier you want is literally in the file, which is why
lexical above carries more weight than vector-search-first intuition suggests.

## Chunking rule (non-negotiable)

Chunk by **AST node** (function / class / method) via Tree-sitter — never by fixed token
count. Each chunk carries: file path, line range, the entity it belongs to, and enough
signature/context to be self-describing. A chunk that splits a function in half poisons
retrieval, so respect syntax boundaries.

**Embed the chunk with its context prefixed.** Before sending a chunk to the embedding
model, prepend its file path, enclosing class, and signature:

```
src/auth/tokens.py › class TokenService › def refresh(self, token: str) -> Token
<the actual chunk source>
```

A bare function body is often ambiguous — `def refresh(self, token)` could be a cache, a
session, or an OAuth token. The prefix tells the embedding model which, so the vector lands
near "authentication" in embedding space instead of somewhere generic. This pattern is
called **contextual retrieval**, and it is the cheapest large recall gain available at this
step: one f-string, no extra API calls, no schema change. Store the prefixed text as what
was embedded; return the raw chunk to the model.

## Fusion

Run the relevant retrievers, then merge with **Reciprocal Rank Fusion (RRF)** — a
simple, robust rank-merge that needs no score calibration across retrievers. Return a
single deduplicated, ranked list where each result knows its `file`, `line range`, and
source retriever(s).

Cheap routing before fusion: if the query looks like a bare symbol (`createToken`,
`UserService`), weight lexical; if it's a natural-language question, weight semantic.
When unsure, run both — RRF handles the merge.

**Then rerank.** RRF is good at *merging* rankings but knows nothing about the query's
meaning — it only sees positions. So fuse wide and cut narrow: take the top ~30 fused
results, score each one against the query with a reranker, and pass only the top ~8 to the
model. Costs roughly 200 ms; buys a large precision gain, because the model's answer
quality depends far more on what's in the top 8 than on what's in the top 30.

### Latency budget

Retrieval sits directly in the user's perceived response time, so treat these as design
constraints, not optimizations to do later:

- **Route before you embed.** A bare-identifier query (`^[A-Za-z_]\w*$`) resolves through
  lexical's exact-match path in ~10 ms. Sending it through an embedding API call first
  adds ~100 ms for a worse answer. Skip the call entirely.
- **Keep the prompt prefix stable.** System prompt → tool definitions → repo map, in that
  order and byte-identical across every question about a repo. OpenAI caches long prompt
  prefixes automatically, so this costs nothing to arrange and pays on every follow-up
  question. It also means: never interpolate a timestamp, request ID, or the user's
  question into the system prompt — one changed byte early in the prefix invalidates
  everything after it.
- **Stream tool-call status, not just tokens.** An agent loop can take 10 s. Showing
  *"searching for `TokenService`… reading `auth/tokens.py`…"* is the difference between
  that feeling alive and feeling hung.

## How the agent uses it (LangGraph)

The retriever is exposed to the agent as **tools**, not a single pre-baked context blob:

- `code_search(query)` → hybrid retrieval, ranked snippets with citations
- `read_file(path, start?, end?)` → exact source for a location
- `list_dependencies(path)` → what a file imports / what imports it

The agent iterates like a developer: search → read a result → realize it needs a caller →
search again → answer. Every answer cites concrete `file:line` locations pulled from
tool results.

### Repo map (agent orientation)

Agentic search has one weak moment: the *first* tool call. The agent lands in a repo it has
never seen and has to guess a search term with no sense of the codebase's shape — worst of
all on vague questions ("how is auth implemented?") where the right keyword isn't obvious.
A wrong first guess means wandering or a miss. This is the same thing Claude Code does
(grep → read → follow → answer, never loading the whole repo), and it's where that style is
weakest.

The fix is a **repo map**: a compressed, names-only table of contents — each file with its
top-level symbols, no bodies — placed in the stable prompt prefix *before* the agent's first
move, so it orients from a floor plan instead of guessing blind. Same idea aider uses.

It's built entirely from data M4 already persisted — **no AI calls, no embeddings**:
`code_entity` supplies the symbol names, `dependency_edge` supplies the import graph. A big
repo has too many symbols to show them all, so rank them with **PageRank over the import
graph**: a file that many files import is probably central, so its symbols earn a spot on
the map; leaf files nobody imports get trimmed. Known caveat: raw centrality over-ranks
generic utilities (a `utils.py` everyone imports floats to the top), which PageRank dampens
but doesn't eliminate — acceptable, because the map only needs to *orient* the agent, which
then verifies by reading real files.

Ships in **M6** with the agent (it's an orientation tool, not a fusion retriever), and lives
in the byte-stable prompt prefix — so OpenAI's automatic prompt caching keeps it free on
every follow-up question about the same repo (see the Latency-budget note above).

**Citations come from tool-result metadata, never from the model.** The retriever already
knows the file and line range of every hit; carry that through and render it. Asking the
model to report where it found something invites it to drift a few lines, or to cite a file
it reasoned about but never opened. This is the difference between a citation the user
trusts and one they stop clicking.

There is no separate single-shot RAG version. It's tempting to think "one retrieval call,
no loop" is the simpler first step, but once the retrievers are already exposed as tools,
the loop is a handful of lines on top — and the single-shot version would be thrown away
immediately after. Ship the agent.

## Build order & measurement

The retrievers do not get built at once. **Build them in cost order — cheap first,
measure, then buy the expensive one.**

| | Build cost | Cost to redo | Ships in |
|---|---|---|---|
| Lexical | one migration (`tsvector` over `file.content`, which clone already persists) | trivial | M4 |
| ~~Structural~~ | fell out of the symbol table being built anyway | trivial | M4 → **removed after M5 measurement** |
| Semantic | chunking + embedding pipeline + pgvector index + tuning | **re-embed the entire corpus** | M7 (conditional) |

The asymmetry in the third column is the whole argument. Chunking strategy is the thing
most likely to change once you see real queries fail — and changing it means paying the
expensive operation again. So the sequence is: ship lexical (+ structural, briefly — see
the decision record above), build the eval set, run the agent against it, find out *which
questions actually fail and why*, and only then build semantic retrieval — with the tuning
knobs (chunk granularity, `k`, threshold, whether the context prefix helps) set against
evidence instead of intuition.

The other benefit is diagnostic. If several fused retrievers return junk, you cannot tell
which one is at fault. Starting with one gives you a clean baseline to attribute every
later regression against — which is exactly what made the structural ablation possible.

**The rule: no retriever joins the fusion without a `recall@k` movement that justifies
it — and, symmetrically, none stays in it without one either.** This makes semantic
retrieval **conditional**: if lexical + the agent + the repo map already clear the eval
bar, M7 is not built at all. Embeddings have to *earn* their slot by moving `recall@k` on
questions the cheap stack demonstrably fails — they are not a foregone conclusion.

## Deferred: call-graph extraction

`dependency_edge` is a **file-level import graph** ("does `a.py` import `b.py`"), not a
call graph. Nothing in Noetra currently answers "who calls `authenticate`?" or "what
implements `BaseAuthenticator`?" — that needs call-site and inheritance extraction in
`indexer/parser.py` plus a new edge type, none of which exists yet.

Not building it speculatively, for the same reason structural retrieval got cut above:
no measured need yet. **Only after** M6 ships and you hit a real question the agent can't
answer because it needs "who calls this" and neither `list_dependencies` nor semantic
search gets there, consider call-graph extraction as its own scoped feature — built
against evidence, like everything else in this doc.

## Evaluation

Retrieval quality is the whole game, and "the answers feel good" is not a measurement.

Build a small eval set — around 40 questions across 2–3 pinned public repos (pinned to a
commit SHA, so the answers don't move). Each question records the file paths and line
ranges that actually contain the answer:

```yaml
- q: "Where is the session cookie signed?"
  repo: noetra-fixtures/flask-sample@a1b2c3d
  answers:
    - { path: "app/session.py", lines: [40, 68] }
```

A pytest runs each question through `core/retrieval` and reports **recall@5** and
**recall@20** — of the questions, how many had a correct location somewhere in the top 5 /
top 20 hits. Recall is the right metric here rather than precision, because the reranker
and then the model both get a chance to discard bad hits; what they cannot do is recover a
correct file that retrieval never surfaced.

Keep it cheap enough to run on every retrieval change. This number is the scoreboard: it's
what tells you whether contextual prefixes helped, whether reranking was worth 200 ms, and
whether semantic retrieval earned its place. It's also the single most interview-legible
artifact in the project — *"lexical-only recall@5 was 0.61; adding AST-chunked embeddings
took it to 0.84"* is a much stronger claim than *"I integrated pgvector."*

## Build note

`core/retrieval` owns all of this. Both the `/search` endpoint and the agent tools call
into it. No retrieval logic lives anywhere else.
