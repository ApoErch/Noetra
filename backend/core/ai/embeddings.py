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
# ...and rejects (400) any single request past 300,000 tokens in total. A page of 200 chunks
# is fine for source files but a repo with big lockfile/markdown gap chunks blew through it
# (357-chunk noetra import: 200 × up to 8k). Not rate-limit machinery — a hard size limit.
_MAX_REQUEST_TOKENS = 250_000


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


def _truncate(text: str) -> tuple[str, int]:
    """Cut a text to at most _MAX_INPUT_TOKENS tokens; returns the text and its token count."""
    tokens = _encoding().encode(text)
    if len(tokens) <= _MAX_INPUT_TOKENS:
        return text, len(tokens)
    return _encoding().decode(tokens[:_MAX_INPUT_TOKENS]), _MAX_INPUT_TOKENS


def _batches(texts: Sequence[str]) -> list[list[str]]:
    """Group truncated texts into consecutive batches whose token total stays under the request cap."""
    batches: list[list[str]] = [[]]
    used = 0
    for text in texts:
        cut, count = _truncate(text)
        if batches[-1] and used + count > _MAX_REQUEST_TOKENS:
            batches.append([])
            used = 0
        batches[-1].append(cut)
        used += count
    return batches


def _embed(texts: Sequence[str]) -> list[list[float]]:
    """Embed `texts` (in as many requests as the size cap needs); one unit-length vector per input, in order."""
    settings = get_settings()
    vectors: list[list[float]] = []
    for batch in _batches(texts):
        response = _client().embeddings.create(model=settings.openai_embedding_model, input=batch)
        # The API returns items in input order, but sort by index anyway — it is what the field is for.
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    return vectors


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed chunk `embed_text` values for indexing."""
    return _embed(texts)


def embed_query(text: str) -> list[float]:
    """Embed one search query."""
    return _embed([text])[0]
