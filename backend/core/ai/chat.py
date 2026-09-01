from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel

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

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=settings.gemini_chat_model, api_key=settings.gemini_api_key)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.openai_chat_model, api_key=settings.openai_api_key)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=settings.anthropic_chat_model, api_key=settings.anthropic_api_key)

    raise ValueError(f"unknown chat provider: {provider!r}")
