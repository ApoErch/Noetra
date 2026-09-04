import uuid
from collections import defaultdict

from core.retrieval.types import RetrievalHit

# The RRF dampening constant. Larger k flattens the influence of the very top ranks. The
# paper's (Cormack et al., 2009) default of 60 was tuned on lists ~1,000 deep; on our
# 20-deep lists it made rank nearly meaningless — "appears in both legs at rank 40"
# outscored "rank 2 in one leg" and the shared mediocre tail crowded out every leg's best
# hit (docs/CONCEPTS.md A22). Measured on the eval: k=10 restores those hits, k<10 buys
# nothing more.
_RRF_K = 10

# A hit's identity for dedup: the exact code location. The same location surfaced by two
# retrievers fuses into one hit whose score is the sum of both contributions.
_Key = tuple[uuid.UUID, int, int]


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievalHit]],
    *,
    weights: tuple[float, ...] | None = None,
    k: int = _RRF_K,
    limit: int = 20,
) -> list[RetrievalHit]:
    """Merge ranked lists by weighted Reciprocal Rank Fusion: score = sum of w_i / (k + rank_i).

    RRF uses each hit's *rank position*, never the retrievers' raw scores — so two
    retrievers whose scores live on different scales (lexical's `ts_rank` vs semantic's
    cosine similarity) never have to be put on a common scale. `weights` (one per list,
    default all 1.0) sizes each leg's vote: equal votes let a weak leg out-vote a strong
    one's correct pick, so search() passes semantic a 2x weight (docs/CONCEPTS.md A22).
    A location that ranks well in several lists rises; dedup unions the source retrievers.

    Called from core/retrieval/__init__.py::search() whenever both the lexical and
    semantic (M6) legs run. M7's graph leg joins this same fusion as a third ranked list.
    """
    if weights is None:
        weights = (1.0,) * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(f"{len(weights)} weights for {len(ranked_lists)} ranked lists")

    scores: dict[_Key, float] = defaultdict(float)
    merged: dict[_Key, RetrievalHit] = {}

    for hits, weight in zip(ranked_lists, weights, strict=True):
        for rank, hit in enumerate(hits, start=1):  # 1-indexed rank for the formula
            key = (hit.file_id, hit.start_line, hit.end_line)
            scores[key] += weight / (k + rank)
            if key not in merged:
                merged[key] = hit
                continue
            # Same location from another retriever: union sources, keep the richer metadata.
            existing = merged[key]
            for source in hit.sources:
                if source not in existing.sources:
                    existing.sources.append(source)
            # Keep whichever retriever's hit actually resolved a match_line, if either did.
            if existing.match_line is None and hit.match_line is not None:
                existing.match_line = hit.match_line
                existing.snippet = hit.snippet

    for key, hit in merged.items():
        hit.score = scores[key]  # overwrite native score with the fused score
    return sorted(merged.values(), key=lambda h: h.score, reverse=True)[:limit]
