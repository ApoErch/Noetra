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
| status | enum | `queued\|cloning\|parsing\|chunking\|embedding\|graphing\|metrics\|ready\|failed` |
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
| language | enum | `python\|javascript\|typescript` |
| content_hash | text | incremental re-index |
| loc | int | lines of code |

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
| content | text | the chunk source |
| embedding | vector(N) | pgvector; N = embedding dim |

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

## Indexes to add early

- `code_entity(repository_id, name)` — structural symbol lookup
- `file` full-text (`tsvector`) + `pg_trgm` on content/identifiers — lexical retrieval
- pgvector index (HNSW or IVFFlat) on `chunk.embedding` — semantic retrieval
- `file(repository_id, content_hash)` — incremental re-index

## Deferred to V2 (not created in V1)

- `security_finding` (security scanner)
- advanced-metric keys (dead code, duplicates, unused imports, complexity, ownership)
