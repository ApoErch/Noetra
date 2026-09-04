# Noetra

Noetra is an engineering intelligence platform. Point it at a GitHub repository and it
clones, indexes, and lets you **search and chat with the codebase** through an AI agent
that answers with exact `file:line` citations.

The core idea is **agentic RAG**: instead of one fixed "embed the question, fetch the top
matches, answer" pass, an agent decides *per question* how to search. An exact identifier
resolves in a single keyword lookup. A vague question can search, read a file, then search
again with better terms — the way a developer explores an unfamiliar codebase.

## What it does

- Log in with **GitHub OAuth**.
- **Import a repository** — a public URL, or a private one using your own GitHub token.
- Noetra clones it and indexes it in the background. As indexing progresses you get, in
  order: a browsable **file tree**, **keyword search**, **semantic search**, then **AI
  chat** and a basic **dashboard** (file/function counts, LOC, language breakdown).
- **Ask questions in chat.** The agent searches the code, reads files, follows imports,
  and answers with citations that link straight to the real file and line.

Supported languages: **Python, JavaScript, TypeScript**.

## How it works

When you import a repo, a background worker clones it and runs it through a pipeline:

- **Clone** — pull the code down, save every file's path and content.
- **Parse** — walk each file with Tree-sitter and extract its functions, classes, and
  imports.
- **Graph** — turn those imports into a dependency graph (which file imports which).
- **Chunk** — split each file along function/class boundaries, so search results are
  always a whole unit of code, never half of one.
- **Embed** — turn each chunk into a vector for semantic (meaning-based) search.
- **Metrics** — count files, functions, lines, and languages.

Search combines two methods and merges their results by rank:

- **Lexical search** — plain keyword/full-text search, best for identifiers, error
  messages, and config values that are literally in the code.
- **Semantic search** — vector similarity search, best for conceptual questions where the
  answer doesn't share any words with the question.

The chat agent calls these same searches as tools, along with a file-reading tool, so it
can chain multiple lookups together instead of being limited to one search per question.
Citations always come from the actual search results, not from the model's memory.

## Stack

- **Frontend** — React, TypeScript, Vite, Tailwind, Monaco (the VS Code editor component)
  for read-only code viewing, TanStack Query for data fetching/caching.
- **API** — FastAPI + Pydantic for a validated REST API, SQLAlchemy for the database
  layer, Alembic for migrations.
- **Background jobs** — Celery + Redis, for cloning and indexing repos outside the
  request/response cycle.
- **Database** — PostgreSQL with the `pgvector` extension, so embeddings live in the same
  database as everything else.
- **Parsing** — Tree-sitter, one parser API with a grammar per language.
- **Agent** — LangGraph, running a hand-written loop: search, read, decide, answer.
- **AI provider** — OpenAI (`text-embedding-3-small` for embeddings, `gpt-4o-mini` for chat
  by default; Anthropic available for chat).
- **Local infra** — Docker Compose (Postgres, Redis, API, worker, frontend).

## Running it locally

**Prerequisites:** Docker + Docker Compose, Python 3.11+ with `uv`, Node 20+ with `pnpm`,
a GitHub OAuth app (client ID + secret), and an [OpenAI API key](https://platform.openai.com/api-keys).

```bash
# copy environment templates and fill in your own secrets
cp .env.example .env
cp web/.env.example web/.env

# start Postgres + Redis, then build and start the API and worker
docker compose up -d db redis
docker compose up --build api worker

# apply database migrations
docker compose exec api alembic upgrade head

# start the frontend
cd web && pnpm install && pnpm dev
```

- Frontend: `http://localhost:5173`
- API: `http://localhost:8000` (interactive docs at `/docs`)

Log in with GitHub, import a repository by URL, and watch it move through indexing.
