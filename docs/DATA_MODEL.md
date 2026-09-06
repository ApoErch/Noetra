# Data Model

PostgreSQL + `pgvector`. SQLAlchemy models in `core/models.py`. Everything scoped by
`repository_id`. Types are conceptual — refine in implementation.

## Entities

### user
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| github_id | bigint | unique |
| username | text | |
| avatar_url | text | |
| access_token | text | encrypted GitHub OAuth token |
| created_at | timestamptz | |

### repository
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| user_id | uuid (fk → user) | owner |
| source | enum | `github_public` \| `github_private` \|
| full_name | text | e.g. `microsoft/vscode` |
| clone_url | text |
| status | enum | `queued\|cloning\|parsing\|graphing\|chunking\|embedding\|metrics\|ready\|failed` (advanced in that order — see `WORKFLOW.md`). All nine values were created in the initial `repository_status` type, so the M9 metrics stage needed no enum migration to start using the last two. `ready` is set in exactly one place: `worker/metrics.py` |
| error_message | text | nullable |
| created_at / updated_at | timestamptz | `updated_at` moves on every status write, which is also what invalidates the repo-map cache |

**No `progress` column, deliberately.** An earlier draft of this table specified `progress`
(0–100) and `indexed_at`; neither was ever built and neither is needed. The UI's step counter
and progress bar are the status value's *index* in the pipeline order
(`web/src/lib/repos.ts`), which answers the same question without a column that every stage
would have to remember to update.

### file
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| path | text | repo-relative |
| language | enum, nullable | `python\|javascript\|typescript`; null for non-parsed files (e.g. `.md`, `.json`) |
| content | text, nullable | full raw file source; populated at clone time for every tracked file (`git ls-files`), not just py/js/ts |
| is_binary | boolean | true for images/compiled assets/etc — `content` stays null, file endpoint returns "can't preview this" instead of dumping binary into a TEXT column |
| content_hash | text | incremental re-index |
| loc | int, nullable | lines of code; only set for parsed languages |
| content_tsv | tsvector, generated | **powers lexical retrieval.** A Postgres *generated* column (`to_tsvector('english', coalesce(content, ''))`) with a GIN index — it maintains itself on every insert/update, so there is no indexing step to run and nothing to keep in sync. Created in the same migration as the table's M4 changes, which is what makes search work the moment cloning finishes. |

### code_entity  *(the symbol table — powers chunking and the M7 repo map; no longer powers retrieval, see `RETRIEVAL.md`)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| file_id | uuid (fk) | |
| kind | enum | `function\|class\|method` |
| name | text | indexed for lookup |
| signature | text | |
| start_line | int | |
| end_line | int | |

### chunk  *(AST-aware unit — powers semantic retrieval)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| file_id | uuid (fk) | |
| entity_id | uuid (fk → code_entity) | nullable |
| start_line | int | |
| end_line | int | |
| content | text | the chunk source, as returned to the model |
| embed_text | text | what was actually embedded: the context prefix (`path › class › signature`) plus `content`. Stored so a re-embed is reproducible and so you can see what the model saw. Also what `content_tsv` is generated over, so the lexical leg gets the context prefix for free. See `RETRIEVAL.md` → chunking rule. |
| content_tsv | tsvector, generated | **powers lexical retrieval.** `to_tsvector('english', coalesce(embed_text, ''))`, GIN-indexed. Self-maintaining, like `file.content_tsv`. |
| embedding | vector(1536), **nullable** | pgvector. 1536 = `text-embedding-3-small`'s native size (and under pgvector's 2000-dim cap for indexing the plain `vector` type, should an ANN index ever be added). Vectors arrive unit-normalized from the provider. Changing the embedding model = a migration here + a full re-embed. **Nullable is load-bearing**: the embedding stage selects `WHERE embedding IS NULL`, so a crash or provider failure resumes instead of restarting the whole repo. |

### dependency_edge  *(file-level import graph — `list_dependencies`; V2 architecture view)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| from_file_id | uuid (fk) | |
| to_file_id | uuid (fk) | |
| kind | enum | `import` |

### reference_edge  *(call graph — powers the `find_references` agent tool; M8, shipped 2026-09-06)*

Distinct from `dependency_edge`: that one is **file → file** ("does `a.py` import `b.py`"),
this one is **entity → entity** ("does `handler()` call `decrypt_token()`"). Resolution is
**name-based** against the symbol table — no type inference (`CONCEPTS.md` B26) — in tiers:
same file, then a file this one imports, then a unique repo-wide name. That's the standard
"poor man's call graph"; it's approximate on purpose, because it only has to orient an agent
that then reads the real file. Never fused into `search()` — see `RETRIEVAL.md`.

**There is no ambiguous tier.** The original spec wrote one row per candidate at confidence
0.3 while every tool filtered at `≥ 0.5` — rows written, indexed, and never read. More to the
point, the error is asymmetric: a wrong edge sends the agent to unrelated code and it answers
from there, while a missing edge only leaves it searching, which it is already good at. So a
tier matching more than one candidate resolves to **nothing**, and does not fall through to a
weaker tier. `CONCEPTS.md` B24 has the reasoning and the measurement (on `requests`, where
`request` is defined twice, all 19 edges resolved to the correct definition).

Written by `indexer/calls.py` during the `graphing` stage — a second Tree-sitter walk that
descends *into* function bodies, which the symbol-table walk in `indexer/parser.py`
deliberately does not. A call site with no enclosing entity (a module-level
`const x = f()`) is dropped, since `from_entity_id` is not nullable.

| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| from_entity_id | uuid (fk → code_entity) | the caller |
| to_entity_id | uuid (fk → code_entity) | the callee |
| line | int | the call site, for citations |
| confidence | float | resolution tier: 0.9 same file · 0.85 imported file · 0.7 unique repo-wide. Ambiguous names write no row at all. Tools filter at `≥ 0.5`, which excludes nothing today — it is the contract that lets a weaker tier be added later without every caller silently inheriting its guesses. |

### metric  *(the dashboard's aggregates — M9, shipped 2026-09-06)*

Written once by the `metrics` pipeline stage (`worker/metrics.py`), read by
`GET /repos/{id}/metrics`. Computed at index time rather than per request because they
describe the *indexed snapshot*: a 36,000-file repo would otherwise re-aggregate on every
dashboard poll. Rewritten wholesale on a re-index (delete-then-insert), so a key that stops
being produced cannot linger as a stale row.

`value` is jsonb precisely so the five V1 keys and any later one share one table with no
migration — a count is stored as an object, a breakdown as a list.

| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk, cascade) | unique together with `key` |
| key | text | `file_count\|function_count\|total_loc\|language_breakdown\|largest_files` |
| value | jsonb | payload |

**Three of the five describe source files only, and say so.** `file.language` and `file.loc`
are populated in one place (`worker/indexing.py`) and only for py/js/ts, so `total_loc`,
`language_breakdown` and `largest_files` exclude Markdown, JSON, config and binaries.
`file_count` therefore reports `{total, source}` rather than one number — the gap between
them is the part the other three cannot see, and hiding it would make the dashboard quietly
wrong rather than merely narrow.

| key | value |
|---|---|
| `file_count` | `{"total": n, "source": n}` |
| `function_count` | `{"function": n, "class": n, "method": n}` — zero-filled, so a repo with no classes reports 0 rather than omitting the key |
| `total_loc` | `{"source_loc": n}` |
| `language_breakdown` | `[{language, files, loc}]`, ordered by loc desc; the null-language group is dropped, not rendered as a bucket |
| `largest_files` | `[{path, loc}]`, top 10. No language field — the frontend derives one from the path, and `loc` is only ever set alongside `language` |

### chat_conversation  *(one chat thread: user × repository — the agent's memory scope)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| user_id | uuid (fk → user) | ownership-checked on every read |
| repository_id | uuid (fk) | |
| title | text | first question, truncated |
| created_at / updated_at | timestamptz | list is ordered by `updated_at` |

### chat_message  *(one question or one final answer — never a tool result)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| conversation_id | uuid (fk → chat_conversation, cascade) | |
| role | enum | `user\|assistant` |
| content | text | the answer keeps its `[path:a-b]` citations inline |
| citations | jsonb, nullable | list of `RetrievalHit` dicts — only citations the verifier backed |
| tool_trace | jsonb, nullable | `[{name, args, summary}]` — for the UI "steps" block, never replayed to the model |
| created_at | timestamptz | |

Only these two roles are stored, and only they are replayed as history (last 12 messages per
turn). Tool results live in the graph state for one turn and are gone — that is what keeps
the prompt small and cacheable across a long conversation (`RETRIEVAL.md`, Memory). A
LangGraph checkpointer was considered and rejected for V1 (`CONCEPTS.md` B22).

## Relationships

```
user 1───* repository 1───* file 1───* code_entity *───* reference_edge
                       │           │        ▲              (entity → entity: calls)
                       │           │        │
                       │           1───* chunk ──┘ (chunk.entity_id, nullable —
                       │           │              maps a graph tool's entity back to
                       │           │              its chunk, so citations stay aligned)
                       │           *───* dependency_edge  (file → file: imports)
                       ├───* metric
                       └───* chat_conversation 1───* chat_message
```

`chunk.entity_id` is how a graph tool result (an *entity*) becomes a chunk-aligned
`RetrievalHit` — the same `path:start-end` shape every other tool emits, so the agent's
citations never mix entity ranges with chunk ranges. Gap chunks have it null; they are
never the target of a call edge.

## Indexes, and when each lands

"Early" is doing real work here — the first two arrive well before the third, and that
staging is what the build order in `CLAUDE.md` rests on.

| index | purpose | milestone |
|-------|---------|-----------|
| GIN on `file.content_tsv` | lexical retrieval over whole files | **M4** — one migration, no pipeline cost |
| `code_entity(repository_id, name)` | symbol lookup (chunking, call resolution, M7 repo map) | **M4** |
| `file(repository_id, content_hash)` | incremental re-index | **M4** |
| GIN on `chunk.content_tsv` | lexical retrieval over chunks (what `search()` actually uses) | **M5** |
| `reference_edge(repository_id, to_entity_id)` | `find_references(direction="callers")` — the reverse direction, which is the one that needs the index; the forward direction is served by the `from_entity_id` FK index | **M8** ✅ |
| `metric(repository_id)` + unique `(repository_id, key)` | the dashboard reads all five rows for one repo; the unique constraint is what makes "one row per key" a schema rule rather than a convention | **M9** ✅ |

**No vector index in V1 (deferred, not forgotten).** `chunk.embedding` has no HNSW/IVFFlat
index — `semantic_search()` does an exact `<=>` cosine scan. Two reasons: the per-file cap
(`row_number() OVER (PARTITION BY file_id ...)`) already forces a full sort over every
matching chunk, so an ANN index would sit unused by the only query that reads this column;
and at V1 scale (hundreds to a few thousand chunks per repo) an exact scan is tens of
milliseconds — faster to add than to need. This is the *brute-force vs. ANN crossover*:
below roughly 10⁵ vectors per filter partition, exact search wins on both correctness and
often latency. Revisit if per-repo chunk counts grow enough that the scan shows up in the
latency budget — at that point it'd be `HNSW ... USING hnsw (embedding vector_cosine_ops)`,
and note pgvector 0.8+'s `hnsw.iterative_scan` would also be needed, since a plain HNSW
index returns global top-k *before* the `repository_id` filter applies, silently returning
fewer rows than requested.

## Deferred to V2 (not created in V1)

- `security_finding` (security scanner)
- advanced-metric keys (dead code, duplicates, unused imports, complexity, ownership)
