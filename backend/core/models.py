import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class User(Base):
    """A logged-in GitHub user who owns imported repositories; holds their encrypted access token."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepositoryStatus(str, enum.Enum):
    """The stages of the indexing pipeline a repo moves through, from `queued` to `ready` (or `failed`)."""

    QUEUED = "queued"
    CLONING = "cloning"
    PARSING = "parsing"
    GRAPHING = "graphing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    METRICS = "metrics"
    READY = "ready"
    FAILED = "failed"


class Repository(Base):
    """A GitHub repo a user imported, plus its current indexing status and any failure message."""

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("user_id", "github_url", name="uq_repositories_user_id_github_url"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    github_url: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[RepositoryStatus] = mapped_column(
        SAEnum(RepositoryStatus, name="repository_status"), default=RepositoryStatus.QUEUED
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Language(str, enum.Enum):
    """A file's detected source language. Null on `file.language` for anything not py/js/ts (e.g. `.md`, `.json`) — those are never parsed."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class File(Base):
    """A single tracked file from a cloned repo: its path, raw content (if text), and a hash for re-index skipping."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String)
    language: Mapped[Language | None] = mapped_column(SAEnum(Language, name="file_language"), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_binary: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String)
    loc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A Postgres *generated* column: Postgres computes and stores this itself on
    # every insert/update, so there's no separate indexing step and nothing for
    # the app to keep in sync — it just maintains itself. `persisted=True` means
    # it's stored on disk (required for this to be indexable), not recomputed
    # on every read.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(content, ''))", persisted=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("repository_id", "path", name="uq_files_repository_id_path"),
        Index("ix_files_repository_id_content_hash", "repository_id", "content_hash"),
        # Declared here, not only in the migration that created it: autogenerate compares the
        # model against the live database, so an index the model doesn't mention reads as one
        # nobody asked for, and every later autogenerate proposes dropping it.
        Index("ix_files_content_tsv", "content_tsv", postgresql_using="gin"),
    )


class EntityKind(str, enum.Enum):
    """What a `code_entity` row represents — one of the three shapes `indexer.parser.extract` produces."""

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"


class CodeEntity(Base):
    """One function/class/method extracted from a file by Tree-sitter — the symbol table (see docs/DATA_MODEL.md)."""

    __tablename__ = "code_entities"
    __table_args__ = (Index("ix_code_entities_repository_id_name", "repository_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EntityKind] = mapped_column(SAEnum(EntityKind, name="entity_kind"))
    name: Mapped[str] = mapped_column(String)
    signature: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    """One AST-aligned slice of a file — the unit hybrid retrieval actually returns (see docs/DATA_MODEL.md).

    Chunks are what make a citation exact: a file-level hit has to guess which line to point
    at, whereas a chunk already knows the line range it covers. `indexer.chunker.chunk_file`
    produces one per leaf entity plus gap chunks for everything between them.

    `embedding` is nullable on purpose: the M6 embedding stage selects
    `WHERE embedding IS NULL`, so a failure partway through a repo resumes instead of
    restarting, and a repo stuck mid-embed stays lexically searchable throughout.
    """

    __tablename__ = "chunks"
    # The lexical-search index. Declared for the same reason as files.content_tsv's above.
    __table_args__ = (Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    # Null for a gap chunk — imports, module constants, a class header, or any file with no
    # entities at all (markdown, JSON). Those are still worth retrieving, just not symbols.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("code_entities.id", ondelete="SET NULL"), nullable=True
    )
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embed_text: Mapped[str] = mapped_column(Text)
    # Indexed over `embed_text`, not `content`, so the file path and enclosing class name
    # are searchable alongside the body — contextual retrieval applied to the lexical leg
    # (docs/RETRIEVAL.md, "chunking rule"). Generated, so Postgres maintains it itself.
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(embed_text, ''))", persisted=True),
        nullable=True,
    )
    # 1536 = text-embedding-3-small's native size (also under pgvector's 2000-dim cap for
    # indexing the plain `vector` type, should an ANN index ever be added). Changing the
    # embedding model means a migration here plus a full re-embed.
    # Explicit Vector(1536) type means the `list[float] | None` annotation is never
    # consulted, so no `type_annotation_map` entry or `# type: ignore` is needed.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DependencyKind(str, enum.Enum):
    """The relationship a `dependency_edge` represents. Only "import" exists in V1."""

    IMPORT = "import"


class DependencyEdge(Base):
    """One resolved import: `from_file` imports `to_file`, both within the same repo (see docs/DATA_MODEL.md).

    Powers `list_dependencies` for the chat agent now; the V2 architecture graph viz later.
    No uniqueness constraint — `indexer.graph.resolve_dependencies` already dedupes before
    these get persisted, so a duplicate here would only ever be an app-level bug, not a
    real-world case (e.g. re-importing the same module twice) worth a DB-level rejection.
    """

    __tablename__ = "dependency_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    from_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE")
    )
    to_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"))
    kind: Mapped[DependencyKind] = mapped_column(
        SAEnum(DependencyKind, name="dependency_kind"), default=DependencyKind.IMPORT
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReferenceEdge(Base):
    """One resolved call: `from_entity` calls `to_entity`, both within the same repo (see docs/DATA_MODEL.md).

    Distinct from `DependencyEdge`, which is file -> file ("does a.py import b.py"); this is
    entity -> entity ("does handler() call decrypt_token()"). Powers the agent's
    `find_references` tool. Never a fused retrieval leg — see docs/RETRIEVAL.md.

    `confidence` records which resolution tier produced the edge (0.9 same file, 0.85 a file
    this one imports, 0.7 a unique repo-wide name). There is deliberately no ambiguous tier:
    `indexer.calls.resolve_calls` writes nothing rather than a row per candidate, because a
    wrong edge sends the agent to unrelated code while a missing one only leaves it
    searching. Same dedupe-before-insert discipline as DependencyEdge, so no unique
    constraint.
    """

    __tablename__ = "reference_edges"
    # get_callers is the direction that needs an index: "who calls this entity" scans by
    # target, where get_callees is already covered by the from_entity_id foreign key.
    __table_args__ = (Index("ix_reference_edges_repository_id_to_entity_id", "repository_id", "to_entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("code_entities.id", ondelete="CASCADE"), index=True
    )
    to_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("code_entities.id", ondelete="CASCADE")
    )
    line: Mapped[int] = mapped_column(Integer)  # the call site, so a citation can point at it
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Metric(Base):
    """One aggregate about an indexed repo, keyed by name with a JSON payload (see docs/DATA_MODEL.md).

    Written once by the `metrics` pipeline stage and read by the dashboard, so the numbers
    describe the indexed snapshot rather than being recounted on every page load — a repo
    with tens of thousands of files would otherwise re-aggregate on every poll.

    `value` is JSONB so the five V1 keys and any later one share a single table with no
    migration: a scalar count is stored as an object, a breakdown as a list.
    """

    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("repository_id", "key", name="uq_metrics_repository_id_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String)
    value: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatConversation(Base):
    """One chat thread between a user and one repository — the unit the chat UI lists and the agent's memory is scoped to."""

    __tablename__ = "chat_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatRole(str, enum.Enum):
    """Who wrote a chat message. Tool calls are never stored as messages — only in `ChatMessage.tool_trace`."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(Base):
    """One user question or one final assistant answer (with its verified citations and the tool steps behind it).

    Only these two roles are stored and replayed as history — never the tool results the
    agent read along the way. That is what keeps each turn's prompt small (docs/RETRIEVAL.md,
    memory section). `tool_trace` exists for the UI's "steps" block, not for the model.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[ChatRole] = mapped_column(SAEnum(ChatRole, name="chat_role"))
    content: Mapped[str] = mapped_column(Text)
    # list of RetrievalHit dicts (assistant only); null for user messages
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # list of {name, args, summary} (assistant only)
    tool_trace: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
