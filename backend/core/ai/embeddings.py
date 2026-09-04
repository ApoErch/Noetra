from functools import lru_cache
from typing import Sequence

import tiktoken
from openai import OpenAI

from core.config import get_settings

# OpenAI *rejects* (HTTP 400) any input past 8,192 tokens rather than silently truncating
# it, so one oversized chunk would fail its whole page. Counted with the model's own
# tokenizer, not estimated from chars — a lockfile's hashes tokenize at ~1.5 chars/token
# where prose is ~4, and the first chars/token guess failed on exactly that.
_MAX_INPUT_TOKENS = 8_000


@lru_cache
def _client() -> OpenAI:
    """One cached OpenAI client per process — search() calls embed_query on the request path."""
    settings = get_settings()
    if settings.embedding_provider != "openai":
        raise ValueError(f"unknown embedding provider: {settings.embedding_provider!r}")
    return OpenAI(api_key=settings.openai_api_key)


@lru_cache
def _encoding() -> tiktoken.Encoding:
    """The tokenizer for the configured embedding model, loaded once per process."""
    return tiktoken.encoding_for_model(get_settings().openai_embedding_model)


def _truncate(text: str) -> str:
    """Cut a text to at most _MAX_INPUT_TOKENS tokens, measured with the model's tokenizer."""
    tokens = _encoding().encode(text)
    if len(tokens) <= _MAX_INPUT_TOKENS:
        return text
    return _encoding().decode(tokens[:_MAX_INPUT_TOKENS])


def _embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed `texts` in one request; returns one unit-length vector per input, in order."""
    settings = get_settings()
    response = _client().embeddings.create(
        model=settings.openai_embedding_model,
        input=[_truncate(t) for t in texts],
    )
    # The API returns items in input order, but sort by index anyway — it is what the field is for.
    return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed chunk `embed_text` values for indexing."""
    return _embed(texts)


def embed_query(text: str) -> list[float]:
    """Embed one search query."""
    return _embed([text])[0]
