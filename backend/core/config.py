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

    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimensions: int = 1536

    # Embeddings stay Gemini-only (Anthropic has no embedding API). Chat gets a per-provider
    # key/model pair each so core/ai/chat.py's get_chat_model(provider) can build any of the
    # three on demand — used for testing the M8 agent against a different model.
    default_chat_provider: str = "gemini"  # "gemini" | "openai" | "anthropic"
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_chat_model: str = "claude-sonnet-5"

    session_secret: str = ""
    token_encryption_key: str = ""
    clone_storage_dir: str = "/data/repos"


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton `Settings` so the environment is parsed only once per process."""
    return Settings()
