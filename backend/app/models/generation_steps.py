"""실제 모델 호출과 외부 도구 실행을 생성별로 분리해 기록한다."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class GenerationStep(IdentityTimestamps, Base):
    __tablename__ = "generation_steps"
    __table_args__ = (
        UniqueConstraint("generation_id", "sequence"),
        CheckConstraint("sequence > 0", name="sequence"),
        CheckConstraint("kind IN ('llm','tool')", name="kind"),
        CheckConstraint("status IN ('running','completed','failed','cancelled')", name="status"),
        CheckConstraint("usage_basis IN ('provider','received','waived')", name="usage_basis"),
        CheckConstraint(
            "prompt_tokens >= 0 AND max_output_tokens >= 0 AND budget_tokens >= 0", name="limits"
        ),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="usage"),
        CheckConstraint("(status = 'running') = (completed_at IS NULL)", name="completion"),
    )
    generation_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    call_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    usage_basis: Mapped[str] = mapped_column(String(12), nullable=False, default="waived")
    reason: Mapped[str | None] = mapped_column(String(60))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
