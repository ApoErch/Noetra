# Data Model

PostgreSQL + `pgvector`. SQLAlchemy models in `core/db`. Everything scoped by
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

### code_entity  *(the symbol table — powers structural retrieval)*
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
| embed_text | text | what was actually embedded: the context prefix (`path › class › signature`) plus `content`. Stored so a re-embed is reproducible and so you can see what the model saw. See `RETRIEVAL.md` → chunking rule. |
| embedding | vector(1536) | pgvector. 1536 = OpenAI `text-embedding-3-small`. Switching to `-large` means `vector(3072)` and a migration + full re-embed — or use OpenAI's `dimensions` parameter to truncate `-large` down to 1536 and keep the column as-is. |

### dependency_edge  *(import graph — powers `list_dependencies`; V2 architecture view)*
| field | type | notes |
|-------|------|-------|
| id | uuid (pk) | |
| repository_id | uuid (fk) | |
| from_file_id | uuid (fk) | |
| to_file_id | uuid (fk) | |
| kind | enum | `import` |

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
user 1───* repository 1───* file 1───* code_entity
                       │           │
                       │           1───* chunk (1───1 embedding)
                       │           │
                       │           *───* dependency_edge
                       ├───* metric
                       └───* chat_message
```

## Indexes, and when each lands

"Early" is doing real work here — the first two arrive well before the third, and that
staging is what the build order in `CLAUDE.md` rests on.

| index | purpose | milestone |
|-------|---------|-----------|
| GIN on `file.content_tsv` + `pg_trgm` on `code_entity.name` | lexical retrieval | **M4** — one migration, no pipeline cost |
| `code_entity(repository_id, name)` | structural symbol lookup | **M4** |
| `file(repository_id, content_hash)` | incremental re-index | **M4** |
| HNSW on `chunk.embedding` | semantic retrieval | **M7** — after evals justify it |

HNSW over IVFFlat for the vector index: it needs no training step and no "how many lists?"
tuning pass, and query recall is better at V1 scale. IVFFlat wins on build time at very
large row counts, which is not a problem V1 has.

## Deferred to V2 (not created in V1)

- `security_finding` (security scanner)
- advanced-metric keys (dead code, duplicates, unused imports, complexity, ownership)
