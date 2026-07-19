from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://noetra:noetra@localhost:5432/noetra"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"

    github_client_id: str = ""
    github_client_secret: str = ""
    github_oauth_callback: str = "http://localhost:8000/api/v1/auth/callback"

    anthropic_api_key: str = ""
    embedding_provider: str = ""
    embedding_api_key: str = ""

    session_secret: str = ""
    clone_storage_dir: str = "/data/repos"


@lru_cache
def get_settings() -> Settings:
    return Settings()
