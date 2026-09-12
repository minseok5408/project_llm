from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class UsagePlan(IdentityTimestamps, Base):
    """일반 사용자에게 부여할 토큰 한도의 기본 상품 정보."""

    __tablename__ = "usage_plans"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint(
            "char_length(code) BETWEEN 1 AND 100 AND code ~ '[^[:space:]]'", name="code_length"
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'", name="name_length"
        ),
        CheckConstraint("token_limit >= 0", name="token_limit_nonnegative"),
    )

    code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    token_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class TokenBudget(IdentityTimestamps, Base):
    """사용자별 유효기간과 부여 당시 한도를 보존하는 토큰 예산."""

    __tablename__ = "token_budgets"
    __table_args__ = (
        UniqueConstraint("grant_key"),
        UniqueConstraint("id", "user_id"),
        CheckConstraint(
            "char_length(grant_key) BETWEEN 1 AND 200 AND grant_key ~ '[^[:space:]]'",
            name="grant_key_length",
        ),
        CheckConstraint("grant_fingerprint ~ '^[0-9a-f]{64}$'", name="grant_fingerprint_format"),
        CheckConstraint("starts_at < ends_at", name="period"),
        CheckConstraint("source IN ('plan', 'free_monthly')", name="source"),
        CheckConstraint(
            "source != 'free_monthly' OR plan_id IS NOT NULL", name="free_plan_required"
        ),
        CheckConstraint("token_limit >= 0", name="token_limit_nonnegative"),
        CheckConstraint("used_tokens >= 0", name="used_tokens_nonnegative"),
        CheckConstraint("reserved_tokens >= 0", name="reserved_tokens_nonnegative"),
        # 큰 bigint 값의 합도 오버플로 없이 검사한다.
        CheckConstraint(
            "CAST(used_tokens AS NUMERIC) + CAST(reserved_tokens AS NUMERIC) <= token_limit",
            name="capacity",
        ),
        Index("ix_token_budgets_plan_id", "plan_id"),
        # 선두 user_id는 사용자 외래 키 조회에도 사용된다.
        Index("ix_token_budgets_user_period", "user_id", "starts_at", "ends_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("usage_plans.id", ondelete="RESTRICT")
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="plan", server_default=text("'plan'")
    )
    grant_key: Mapped[str] = mapped_column(String(200), nullable=False)
    grant_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_limit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    reserved_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )


class TokenReservation(IdentityTimestamps, Base):
    """요청별 허용량과 실제 정산을 기록하며 기존 사전 예약 방식도 보존한다.

    deferred 방식의 reserved_tokens는 사용량 상한이며 예산의 예약량을 차감하지 않는다.
    """

    __tablename__ = "token_reservations"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key"),
        UniqueConstraint("id", "user_id", name="uq_token_reservations_identity_user"),
        ForeignKeyConstraint(
            ["budget_id", "user_id"],
            ["token_budgets.id", "token_budgets.user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "char_length(request_key) BETWEEN 1 AND 200 AND request_key ~ '[^[:space:]]'",
            name="request_key_length",
        ),
        CheckConstraint("quota_exempt = (budget_id IS NULL)", name="exemption_budget"),
        CheckConstraint("reserved_tokens > 0", name="reserved_tokens_positive"),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_nonnegative"
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_nonnegative"
        ),
        CheckConstraint("status IN ('reserved', 'settled', 'released')", name="status"),
        CheckConstraint("charge_mode IN ('reserved', 'deferred')", name="charge_mode"),
        CheckConstraint("usage_basis IN ('provider', 'received', 'waived')", name="usage_basis"),
        CheckConstraint(
            "(status = 'reserved' AND usage_basis IS NULL) OR "
            "(status = 'settled' AND usage_basis IS NOT NULL "
            "AND (usage_basis != 'waived' OR (input_tokens = 0 AND output_tokens = 0))) OR "
            "(status = 'released' AND usage_basis IS NOT NULL AND usage_basis = 'waived')",
            name="usage_basis_state",
        ),
        # 상태별 NULL 조건을 명시하여 CHECK의 NULL 허용으로 불완전한 정산이 통과하지 않게 한다.
        CheckConstraint(
            "(status = 'reserved' AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'settled' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND CAST(input_tokens AS NUMERIC) + CAST(output_tokens AS NUMERIC) "
            "<= reserved_tokens) "
            "OR (status = 'released' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "AND input_tokens = 0 AND output_tokens = 0 AND completed_at IS NOT NULL)",
            name="completion_state",
        ),
        Index("ix_token_reservations_budget_id", "budget_id"),
        Index("ix_token_reservations_status", "status"),
        Index(
            "ux_token_reservations_pending_deferred_user",
            "user_id",
            unique=True,
            postgresql_where=text("charge_mode = 'deferred' AND status = 'reserved'"),
        ),
        # 사용자별 이력 조회와 사용자 외래 키 검사를 함께 지원한다.
        Index("ix_token_reservations_user_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    budget_id: Mapped[UUID | None] = mapped_column(PostgresUUID(as_uuid=True))
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    quota_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    charge_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved", server_default=text("'reserved'")
    )
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    usage_basis: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved", server_default=text("'reserved'")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
