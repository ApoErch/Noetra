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
| status | enum | `queued\|cloning\|parsing\|graphing\|chunking\|embedding\|metrics\|ready\|failed` (advanced in that order — see `WORKFLOW.md`) |
| progress | int | 0–100 |
| error | text | nullable |
| indexed_at | timestamptz | nullable |
| created_at | timestamptz | |

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

### code_entity  *(the symbol table — powers chunking and the M6 repo map; no longer powers retrieval, see `RETRIEVAL.md`)*
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
| embedding | vector(1536), **nullable** | pgvector. 1536 = `gemini-embedding-001` truncated from its 3072 default (Matryoshka). Why 1536 and not 3072: pgvector's **HNSW index caps the `vector` type at 2000 dims** — 3072 would force `halfvec`. Vectors are **L2-normalized client-side**, since `gemini-embedding-001` only pre-normalizes at 3072. **Nullable is load-bearing**: the embedding stage selects `WHERE embedding IS NULL`, so a rate-limit failure resumes instead of restarting the whole repo. |

### dependency_edge  *(file-level import graph — `list_dependencies`; V2 architecture view)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| from_file_id | uuid (fk) | |
| to_file_id | uuid (fk) | |
| kind | enum | `import` |

### reference_edge  *(call graph — powers `get_callers`/`get_callees` and the M7 graph leg)*

Distinct from `dependency_edge`: that one is **file → file** ("does `a.py` import `b.py`"),
this one is **entity → entity** ("does `handler()` call `decrypt_token()`"). Resolution is
**name-based** against the symbol table — no type inference — preferring same-file then
imported-file candidates, and writing **all** candidates when a name is genuinely ambiguous.
That's the standard "poor man's call graph"; it's approximate on purpose, because it only
has to orient an agent that then reads the real file.

| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| from_entity_id | uuid (fk → code_entity) | the caller |
| to_entity_id | uuid (fk → code_entity) | the callee |
| line | int | the call site, for citations |

### metric  *(basic only in V1; jsonb so new metrics need no migration)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| key | text | `file_count\|function_count\|total_loc\|language_breakdown\|largest_files` |
| value | jsonb | payload |

### chat_message  *(optional in V1)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| user_id | uuid (fk) | |
| role | enum | `user\|assistant` |
| content | text | |
| citations | jsonb | list of `{file, start_line, end_line}` |
| created_at | timestamptz | |

## Relationships

```
user 1───* repository 1───* file 1───* code_entity *───* reference_edge
                       │           │        ▲              (entity → entity: calls)
                       │           │        │
                       │           1───* chunk ──┘ (chunk.entity_id, nullable —
                       │           │              this is what resolves a search hit
                       │           │              back to a symbol the graph can walk)
                       │           *───* dependency_edge  (file → file: imports)
                       ├───* metric
                       └───* chat_message
```

`chunk.entity_id` carries more weight than it looks. It's how the M7 graph leg turns a
ranked *chunk* into a *seed entity*, and how it turns an expanded entity back into a
chunk-aligned `RetrievalHit` — which RRF requires, since it dedupes on
`(file_id, start_line, end_line)`. Gap chunks have it null and simply don't seed.

## Indexes, and when each lands

"Early" is doing real work here — the first two arrive well before the third, and that
staging is what the build order in `CLAUDE.md` rests on.

| index | purpose | milestone |
|-------|---------|-----------|
| GIN on `file.content_tsv` | lexical retrieval over whole files | **M4** — one migration, no pipeline cost |
| `code_entity(repository_id, name)` | symbol lookup (chunking, call resolution, M8 repo map) | **M4** |
| `file(repository_id, content_hash)` | incremental re-index | **M4** |
| GIN on `chunk.content_tsv` | lexical retrieval over chunks (what `search()` actually uses) | **M5** |
| `reference_edge(repository_id, to_entity_id)` | `get_callers` — the reverse direction, which is the one that needs the index | **M7** |

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
