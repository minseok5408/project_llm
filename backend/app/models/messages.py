from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class Message(IdentityTimestamps, Base):
    """대화 내 순서와 작업 공간 경계를 DB 제약조건으로 보장하는 메시지."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        CheckConstraint("status IN ('pending', 'completed', 'failed', 'cancelled')", name="status"),
        CheckConstraint(
            "(role = 'user' AND created_by IS NOT NULL AND status = 'completed') "
            "OR (role IN ('assistant', 'system') AND created_by IS NULL)",
            name="role_author_status",
        ),
        CheckConstraint(
            "status <> 'completed' OR content ~ '[^[:space:]]'", name="completed_content"
        ),
        CheckConstraint("token_count IS NULL OR token_count >= 0", name="token_count_nonnegative"),
        CheckConstraint(
            "model IS NULL OR char_length(btrim(model)) BETWEEN 1 AND 255", name="model_length"
        ),
        CheckConstraint(
            "prompt_version IS NULL OR char_length(btrim(prompt_version)) BETWEEN 1 AND 255",
            name="prompt_version_length",
        ),
        Index("ix_messages_workspace_id_conversation_id", "workspace_id", "conversation_id"),
        Index("ix_messages_created_by", "created_by"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed", server_default=text("'completed'")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    token_count: Mapped[int | None] = mapped_column(BigInteger)
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(255))
