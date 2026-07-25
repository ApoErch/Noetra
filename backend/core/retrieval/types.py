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
    start_line: int  # 1-indexed
    end_line: int
    snippet: str  # the line(s) shown in the results list
    score: float  # native retriever score pre-fusion; the RRF score post-fusion
    sources: list[RetrieverSource]  # usually one; becomes several once fusion merges a dup
    entity_name: str | None = None  # set by the structural retriever (e.g. "createToken")
    entity_kind: EntityKind | None = None  # function | class | method, for structural hits
