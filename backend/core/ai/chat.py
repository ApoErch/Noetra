from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from core.config import get_settings


@lru_cache
def get_chat_model(provider: str | None = None) -> BaseChatModel:
    """Build a ready-to-use LangChain chat model for `provider` (default: settings.default_chat_provider).

    The one place that picks a chat provider — callers never touch a provider SDK directly,
    so swapping models (e.g. for testing the M8 agent against a different one) is passing a
    different string here, not a code change anywhere else.
    """
    settings = get_settings()
    provider = provider or settings.default_chat_provider

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.openai_chat_model, api_key=SecretStr(settings.openai_api_key))
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # `model_name` is the field's declared name (`model` is a runtime-only alias), and
        # `timeout`/`stop` are required by the typed signature — LangChain's own docs pass
        # them as None too.
        return ChatAnthropic(
            model_name=settings.anthropic_chat_model,
            api_key=SecretStr(settings.anthropic_api_key),
            timeout=None,
            stop=None,
        )

    raise ValueError(f"unknown chat provider: {provider!r}")
