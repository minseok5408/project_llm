from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class Conversation(IdentityTimestamps, Base):
    """작업 공간에 속한 대화와 목록 정렬·메시지 순번 상태."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 300", name="title_length"),
        CheckConstraint("status IN ('active', 'archived')", name="status"),
        CheckConstraint("char_length(btrim(model)) BETWEEN 1 AND 255", name="model_length"),
        CheckConstraint("next_message_sequence > 0", name="next_message_sequence_positive"),
        Index("ix_conversations_created_by", "created_by"),
        Index(
            "ix_conversations_workspace_status_cursor",
            "workspace_id",
            "status",
            text("last_message_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # 빈 대화도 목록 커서로 빠짐없이 조회하도록 생성 시각을 초기 정렬 기준으로 사용한다.
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_message_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
