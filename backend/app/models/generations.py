"""대화 생성 요청의 멱등성, 실행 상태와 재연결 가능한 이벤트를 저장한다."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class GenerationRun(IdentityTimestamps, Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        UniqueConstraint("reservation_id"),
        UniqueConstraint("user_message_id"),
        UniqueConstraint("assistant_message_id"),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','usage_pending')",
            name="status",
        ),
        CheckConstraint("prompt_tokens >= 0 AND max_output_tokens > 0", name="reserved_usage"),
        CheckConstraint("last_event_sequence >= 0", name="event_sequence"),
        CheckConstraint("jsonb_typeof(request_messages) = 'array'", name="messages_array"),
        CheckConstraint("jsonb_typeof(options) = 'object'", name="options_object"),
        CheckConstraint(
            "(status IN ('queued','running') AND completed_at IS NULL) OR "
            "(status IN ('completed','failed','cancelled','usage_pending') "
            "AND completed_at IS NOT NULL)",
            name="completion_state",
        ),
        Index(
            "ix_generation_runs_queue",
            "created_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_generation_runs_conversation", "conversation_id", "created_at"),
        Index(
            "uq_generation_runs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index(
            "uq_generation_runs_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    user_message_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    assistant_message_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("messages.id", ondelete="RESTRICT"), nullable=False
    )
    reservation_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("token_reservations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default=text("'queued'")
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    context_compaction_needed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_event_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(60))


class GenerationEvent(IdentityTimestamps, Base):
    __tablename__ = "generation_events"
    __table_args__ = (
        UniqueConstraint("generation_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("kind IN ('meta','delta','done','error','cancelled')", name="kind"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
    )
    generation_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
