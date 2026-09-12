"""대화의 접근 권한을 따르는 로컬 원본, 처리 버전과 검색 조각."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, REAL
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class Document(IdentityTimestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        Index("ix_documents_conversation", "conversation_id", "deleted_at"),
    )
    workspace_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    filename: Mapped[str] = mapped_column(String(180))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(IdentityTimestamps, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id"),
        CheckConstraint("version > 0 AND byte_size > 0", name="sizes"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        CheckConstraint(
            "status IN ('uploaded','scanning','parsing','indexing','ready','failed')",
            name="status",
        ),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="attempts"),
        Index("ix_document_versions_queue", "status", "created_at"),
    )
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    extension: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="uploaded", server_default="uploaded")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(60))
    parser_version: Mapped[str | None] = mapped_column(String(40))
    embedding_version: Mapped[str | None] = mapped_column(String(160))
    scan_engine: Mapped[str | None] = mapped_column(String(40))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)


class Chunk(IdentityTimestamps, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal"),
        CheckConstraint("ordinal > 0 AND page > 0 AND start_char >= 0", name="location"),
        CheckConstraint("end_char > start_char AND char_length(content) <= 1600", name="content"),
        CheckConstraint("cardinality(embedding) = 384", name="embedding_dimension"),
    )
    version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(REAL))
