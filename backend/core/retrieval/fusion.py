import uuid
from collections import defaultdict

from core.retrieval.types import RetrievalHit

# The RRF dampening constant from the original paper (Cormack et al., 2009). Larger k
# flattens the influence of the very top ranks; 60 is the widely-used default.
_RRF_K = 60

# A hit's identity for dedup: the exact code location. The same location surfaced by two
# retrievers fuses into one hit whose score is the sum of both contributions.
_Key = tuple[uuid.UUID, int, int]


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievalHit]], *, k: int = _RRF_K, limit: int = 20
) -> list[RetrievalHit]:
    """Merge ranked lists by Reciprocal Rank Fusion: score = sum of 1/(k + rank).

    RRF uses each hit's *rank position*, never the retrievers' raw scores — so lexical's
    ts_rank and structural's trigram similarity never have to be put on a common scale.
    A location that ranks well in several lists rises; dedup unions the source retrievers.
    """
    scores: dict[_Key, float] = defaultdict(float)
    merged: dict[_Key, RetrievalHit] = {}

    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):  # 1-indexed rank for the formula
            key = (hit.file_id, hit.start_line, hit.end_line)
            scores[key] += 1.0 / (k + rank)
            if key not in merged:
                merged[key] = hit
                continue
            # Same location from another retriever: union sources, keep the richer metadata.
            existing = merged[key]
            for source in hit.sources:
                if source not in existing.sources:
                    existing.sources.append(source)
            if existing.entity_name is None and hit.entity_name is not None:
                existing.entity_name = hit.entity_name
                existing.entity_kind = hit.entity_kind
            # Structural hits carry no match_line (they match on a name, not on body text),
            # so keep whichever retriever did work one out.
            if existing.match_line is None and hit.match_line is not None:
                existing.match_line = hit.match_line
                existing.snippet = hit.snippet

    for key, hit in merged.items():
        hit.score = scores[key]  # overwrite native score with the fused score
    return sorted(merged.values(), key=lambda h: h.score, reverse=True)[:limit]
