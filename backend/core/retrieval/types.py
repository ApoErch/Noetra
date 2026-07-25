import enum
import uuid

from pydantic import BaseModel

from core.models import EntityKind


class RetrieverSource(str, enum.Enum):
    """Which retriever produced a hit; a fused hit can carry more than one."""

    LEXICAL = "lexical"
    STRUCTURAL = "structural"
    # SEMANTIC = "semantic"  # M7, conditional — the slot is reserved, unused for now


class RetrievalHit(BaseModel):
    """One ranked search result: a file location, its display snippet, and where it came from.

    The single shared type every retriever returns and fusion consumes, so fusion never
    has to special-case which retriever a hit originated from.
    """

    file_id: uuid.UUID
    path: str  # repo-relative — used for the citation and to open the file in Monaco
    start_line: int  # 1-indexed; with the chunk's real boundaries, never a guess
    end_line: int
    snippet: str  # the single line shown in the results list
    score: float  # native retriever score pre-fusion; the RRF score post-fusion
    sources: list[RetrieverSource]  # usually one; becomes several once fusion merges a dup
    entity_name: str | None = None  # set by the structural retriever (e.g. "createToken")
    entity_kind: EntityKind | None = None  # function | class | method, for structural hits
    # Absolute file line of `snippet` — which line inside the cited range actually matched.
    # The range says "this function is the answer"; this says "and this line is why it
    # matched", so the UI can highlight the block and point within it. None for structural
    # hits, which match on a symbol's name rather than on anything inside its body.
    match_line: int | None = None
