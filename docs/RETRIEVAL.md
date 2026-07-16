# Retrieval (the core)

Pure vector search over code gives weak, hand-wavy answers. Noetra uses **hybrid
retrieval**: three retrievers that answer different kinds of question, fused into one
ranked result set. This is the most important part of the product — the agent is only
as good as what this returns.

## The three retrievers

**1. Lexical** — exact/keyword matching.
Postgres full-text (`tsvector`) + trigram (`pg_trgm`) over file content and identifiers.
Answers: exact string/identifier lookups, error messages, config keys.
*"find every place that references `STRIPE_SECRET`."*

**2. Structural (symbol table + graph)** — the code's own structure.
Lookups over `code_entity` (definitions: functions, classes, methods) and
`dependency_edge` (imports between files).
Answers: definitions, and what depends on what.
*"where is `createToken` defined?"* → exact symbol match, no embedding needed.

**3. Semantic** — meaning, via embeddings.
pgvector similarity over **AST-aware chunks** (chunked by function/class, never fixed
token windows). Uses a code-capable embedding model (provider chosen at implementation time).
Answers: conceptual, fuzzy questions where the words don't match the code.
*"how does authentication work?"*

## Chunking rule (non-negotiable)

Chunk by **AST node** (function / class / method) via Tree-sitter — never by fixed token
count. Each chunk carries: file path, line range, the entity it belongs to, and enough
signature/context to be self-describing. A chunk that splits a function in half poisons
retrieval, so respect syntax boundaries.

## Fusion

Run the relevant retrievers, then merge with **Reciprocal Rank Fusion (RRF)** — a
simple, robust rank-merge that needs no score calibration across retrievers. Return a
single deduplicated, ranked list where each result knows its `file`, `line range`, and
source retriever(s).

Cheap routing before fusion: if the query looks like a bare symbol (`createToken`,
`UserService`), weight structural + lexical; if it's a natural-language question, weight
semantic. When unsure, run all three — RRF handles the merge.

## How the agent uses it (LangGraph)

The retriever is exposed to the agent as **tools**, not a single pre-baked context blob:

- `code_search(query)` → hybrid retrieval, ranked snippets with citations
- `find_symbol(name)` → structural definition lookup
- `read_file(path, start?, end?)` → exact source for a location
- `list_dependencies(path)` → what a file imports / what imports it

The agent iterates like a developer: search → read a result → realize it needs a caller →
search again → answer. Every answer cites concrete `file:line` locations pulled from
tool results. The single-shot RAG milestone (build step 7) uses the same retriever with
one `code_search` call and no loop — that's the difference between the two chat versions.

## Build note

`core/retrieval` owns all of this. Both the `/search` endpoint and the agent tools call
into it. No retrieval logic lives anywhere else.
