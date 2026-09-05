"""Mechanical citation verification: keep `[path:start-end]` only if a tool result backs it.

No model call. The regex finds every citation the model wrote; each one must name a path the
agent actually retrieved this turn and a line range overlapping a retrieved range. Anything
else is rewritten as plain text so the UI never renders a link to code the agent never saw.
"""

import re
from dataclasses import dataclass, field

from core.retrieval import RetrievalHit

# `[backend/api/repos.py:90-99]` or `[README.md:12]`. Paths may not contain whitespace, brackets
# or a colon (the colon is the path/line separator).
CITATION_RE = re.compile(r"\[([^\[\]\s:]+):(\d+)(?:-(\d+))?\]")


@dataclass
class VerifiedAnswer:
    """The answer with unbacked citations demoted, plus the citations that survived and those that did not."""

    text: str
    citations: list[RetrievalHit] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)


def _backing_hit(path: str, start: int, end: int, hits: list[RetrievalHit]) -> RetrievalHit | None:
    """The first retrieved hit whose path matches and whose line range overlaps `[start, end]`."""
    for hit in hits:
        if hit.path == path and hit.start_line <= end and hit.end_line >= start:
            return hit
    return None


def verify_citations(text: str, hits: list[RetrievalHit]) -> VerifiedAnswer:
    """Check every `[path:a-b]` in `text` against `hits`; demote unbacked ones to plain `path:a-b`."""
    citations: list[RetrievalHit] = []
    seen: set[tuple[str, int, int]] = set()
    stripped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        """Keep a backed citation verbatim (recording it), or demote an unbacked one to plain text."""
        path, start_s, end_s = match.group(1), match.group(2), match.group(3)
        start = int(start_s)
        end = int(end_s) if end_s else start
        if end < start:
            start, end = end, start
        backing = _backing_hit(path, start, end, hits)
        if backing is None:
            stripped.append(match.group(0))
            return f"{path}:{start}" if start == end else f"{path}:{start}-{end}"
        key = (path, start, end)
        if key not in seen:
            seen.add(key)
            # The cited range, not the whole backing chunk: the UI highlights what the model
            # actually pointed at, while file_id/snippet come from the hit that proves it exists.
            citations.append(
                RetrievalHit(
                    file_id=backing.file_id,
                    path=path,
                    start_line=start,
                    end_line=end,
                    snippet=backing.snippet,
                    score=backing.score,
                    sources=list(backing.sources),
                    match_line=None,
                )
            )
        return match.group(0)

    return VerifiedAnswer(text=CITATION_RE.sub(replace, text), citations=citations, stripped=stripped)
