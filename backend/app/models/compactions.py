"""원본 대화를 유지하면서 재사용할 요약과 시스템 유지 작업의 사용량을 저장한다."""

from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class ConversationCompaction(IdentityTimestamps, Base):
    """대화 압축 시도별 요약 범위와 사용자 예산에 차감하지 않는 모델 사용량."""

    __tablename__ = "conversation_compactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("model ~ '[^[:space:]]'", name="model_nonblank"),
        CheckConstraint("prompt_version > 0", name="prompt_version_positive"),
        CheckConstraint("through_sequence >= 0", name="through_sequence_nonnegative"),
        CheckConstraint("status IN ('running','completed','failed','cancelled')", name="status"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_nonnegative"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_nonnegative"
        ),
        CheckConstraint("usage_basis IN ('provider','received')", name="usage_basis"),
        CheckConstraint(
            "(input_tokens IS NULL AND output_tokens IS NULL AND usage_basis IS NULL) OR "
            "(input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND usage_basis IS NOT NULL)",
            name="usage_state",
        ),
        # 실패하거나 중단한 요약이 다음 요청의 컨텍스트에 섞이지 않도록 원문을 저장하지 않는다.
        CheckConstraint(
            "(status = 'completed' AND content IS NOT NULL AND content ~ '[^[:space:]]' "
            "AND through_sequence > 0 AND input_tokens IS NOT NULL "
            "AND output_tokens IS NOT NULL AND usage_basis IS NOT NULL) OR "
            "(status != 'completed' AND content IS NULL)",
            name="content_state",
        ),
        Index(
            "ix_conversation_compactions_lookup",
            "workspace_id",
            "conversation_id",
            "through_sequence",
        ),
        Index("ix_conversation_compactions_generation_id", "generation_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    generation_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    through_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default=text("'running'")
    )
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    usage_basis: Mapped[str | None] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(60))
