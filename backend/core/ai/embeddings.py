import math
from functools import lru_cache
from typing import Sequence

from google import genai
from google.genai import types

from core.config import get_settings

# gemini-embedding-001 silently truncates any single input past ~2048 tokens. Estimating
# tokens as chars/2.5 OVER-estimates (code is denser than English prose, ~4 chars/token) —
# safe for the batch budget below, but for a single-text ceiling we need the opposite
# direction, so this is deliberately conservative (smaller) rather than exact:
# 2048 tokens * 2.5 chars/token ~= 5000 chars guarantees we cut before the real cap, not after.
_MAX_INPUT_CHARS = 5_000

_CHARS_PER_TOKEN_ESTIMATE = 2.5
# First-guess constants, not measured against a real account's limits (Google serves RPM/TPM
# dynamically per account now, not as a published table — see aistudio.google.com/rate-limit).
# Conservative on purpose; revisit if the embedding stage starts 400ing or pacing feels wrong.
_BATCH_TOKEN_BUDGET = 20_000
_BATCH_MAX_TEXTS = 100


@lru_cache
def _client() -> genai.Client:
    """One cached Gemini client per process — search() calls embed_query on the request
    path, so rebuilding a client per call would waste connection setup on every search.

    Retry is the SDK's job (Google's own exponential backoff), not ours: a 429 on a
    per-minute quota window is recovered by a max_delay past 60s, no hand-rolled loop needed.
    """
    settings = get_settings()
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=5, initial_delay=1.0, max_delay=65.0)
        ),
    )


def _truncate(text: str) -> str:
    """Cut a text to comfortably fit the model's ~2048-token input cap.

    The API truncates silently past that cap — an over-limit text would embed only its
    first ~2048 tokens with nothing telling us that happened. Truncating ourselves first
    means we know exactly what got embedded.
    """
    return text[:_MAX_INPUT_CHARS]


def _normalize(vector: list[float]) -> list[float]:
    """L2-normalize a vector to length 1.0 so cosine distance is meaningful.

    gemini-embedding-001 only pre-normalizes its native 3072-dim output; truncating to 1536
    (Matryoshka) breaks that guarantee. Skipping this makes cosine distance silently wrong —
    degraded recall, no error to catch it.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _batch(texts: Sequence[str]) -> list[list[str]]:
    """Group texts into request-sized batches by estimated token budget, not by count.

    A fixed batch size eventually exceeds the API's per-request token ceiling once a few
    large chunks land in the same batch, regardless of how small the count is. Sizing by
    estimated tokens avoids that failure mode entirely.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0.0
    for text in texts:
        estimated = len(text) / _CHARS_PER_TOKEN_ESTIMATE
        over_budget = current_tokens + estimated > _BATCH_TOKEN_BUDGET
        if current and (over_budget or len(current) >= _BATCH_MAX_TEXTS):
            batches.append(current)
            current, current_tokens = [], 0.0
        current.append(text)
        current_tokens += estimated
    if current:
        batches.append(current)
    return batches


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed chunk `embed_text` values for indexing; returns one vector per input, in order.

    task_type=RETRIEVAL_DOCUMENT — the asymmetric encoder's "this is a thing to be found"
    side (see embed_query for the other side, and docs/RETRIEVAL.md for why they differ).
    """
    settings = get_settings()
    client = _client()
    vectors: list[list[float]] = []
    for batch in _batch(texts):
        response = client.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=[_truncate(t) for t in batch],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=settings.gemini_embedding_dimensions,
            ),
        )
        vectors.extend(_normalize(e.values) for e in response.embeddings)
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed one search query.

    task_type=CODE_RETRIEVAL_QUERY — Google's natural-language-to-code retrieval mode,
    exactly this product's query shape, and the asymmetric counterpart to embed_documents.
    """
    settings = get_settings()
    response = _client().models.embed_content(
        model=settings.gemini_embedding_model,
        contents=[_truncate(text)],
        config=types.EmbedContentConfig(
            task_type="CODE_RETRIEVAL_QUERY",
            output_dimensionality=settings.gemini_embedding_dimensions,
        ),
    )
    return _normalize(response.embeddings[0].values)
