import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
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
