from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration (DB, Redis, OAuth, secrets), loaded from environment variables / `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://noetra:noetra@localhost:5432/noetra"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"

    github_client_id: str = ""
    github_client_secret: str = ""
    github_oauth_callback: str = "http://localhost:8000/api/v1/auth/callback"
    frontend_url: str = "http://localhost:5173"

    # AI providers — read only by core/ai. `embedding_provider` and `default_chat_provider`
    # are the switch points: a new provider is one more branch in core/ai, nothing else.
    embedding_provider: str = "openai"  # only "openai" is implemented today
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"  # 1536 dims = chunk.embedding vector(1536)
    openai_chat_model: str = "gpt-5.4-mini"
    default_chat_provider: str = "openai"  # "openai" | "anthropic"
    anthropic_api_key: str = ""
    anthropic_chat_model: str = "claude-sonnet-5"

    # Chat agent (core/agent). The budget is the hard stop on tool calls per question; the
    # repo map is the token allowance for the ranked table of contents in the prompt prefix.
    agent_tool_budget: int = 8
    agent_repo_map_tokens: int = 1500

    session_secret: str = ""
    # Session cookie lifetime and transport. Both were previously left to Starlette's
    # defaults; they are explicit now because `https_only` must be True in any deployed
    # environment and a silent default is the wrong place for that to live.
    session_max_age_seconds: int = 60 * 60 * 24 * 14
    session_https_only: bool = False
    token_encryption_key: str = ""
    clone_storage_dir: str = "/data/repos"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton `Settings` so the environment is parsed only once per process."""
    return Settings()
