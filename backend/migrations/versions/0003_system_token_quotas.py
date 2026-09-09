"""서비스 역할과 상품·토큰 예산·요청별 예약 기록을 추가한다.

리비전 ID: 0003_system_token_quotas
이전 리비전: 0002_core_chat_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_system_token_quotas"
down_revision: str | Sequence[str] | None = "0002_core_chat_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def identity_columns() -> list[sa.Column]:
    """마이그레이션 당시 정의를 유지하는 독립된 UUID·시각 컬럼을 생성한다."""
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # 기존 사용자를 일반 역할로 유지하며 시스템 계정이나 상품을 자동 생성하지 않는다.
    op.add_column(
        "users",
        sa.Column(
            "platform_role", sa.String(20), server_default=sa.text("'member'"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_users_platform_role"), "users", "platform_role IN ('member', 'system')"
    )

    op.create_table(
        "usage_plans",
        *identity_columns(),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_limit", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint(
            "char_length(code) BETWEEN 1 AND 100 AND code ~ '[^[:space:]]'",
            name=op.f("ck_usage_plans_code_length"),
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'",
            name=op.f("ck_usage_plans_name_length"),
        ),
        sa.CheckConstraint("token_limit >= 0", name=op.f("ck_usage_plans_token_limit_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_plans")),
        sa.UniqueConstraint("code", name=op.f("uq_usage_plans_code")),
    )

    op.create_table(
        "token_budgets",
        *identity_columns(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("grant_key", sa.String(200), nullable=False),
        sa.Column("grant_fingerprint", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_limit", sa.BigInteger(), nullable=False),
        sa.Column("used_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "char_length(grant_key) BETWEEN 1 AND 200 AND grant_key ~ '[^[:space:]]'",
            name=op.f("ck_token_budgets_grant_key_length"),
        ),
        sa.CheckConstraint(
            "grant_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_token_budgets_grant_fingerprint_format"),
        ),
        sa.CheckConstraint("starts_at < ends_at", name=op.f("ck_token_budgets_period")),
        sa.CheckConstraint(
            "token_limit >= 0", name=op.f("ck_token_budgets_token_limit_nonnegative")
        ),
        sa.CheckConstraint(
            "used_tokens >= 0", name=op.f("ck_token_budgets_used_tokens_nonnegative")
        ),
        sa.CheckConstraint(
            "reserved_tokens >= 0", name=op.f("ck_token_budgets_reserved_tokens_nonnegative")
        ),
        sa.CheckConstraint(
            "CAST(used_tokens AS NUMERIC) + CAST(reserved_tokens AS NUMERIC) <= token_limit",
            name=op.f("ck_token_budgets_capacity"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_token_budgets_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["usage_plans.id"],
            name=op.f("fk_token_budgets_plan_id_usage_plans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_budgets")),
        sa.UniqueConstraint("grant_key", name=op.f("uq_token_budgets_grant_key")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_token_budgets_id")),
    )
    op.create_index("ix_token_budgets_plan_id", "token_budgets", ["plan_id"])
    op.create_index(
        "ix_token_budgets_user_period", "token_budgets", ["user_id", "starts_at", "ends_at"]
    )

    op.create_table(
        "token_reservations",
        *identity_columns(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("budget_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_key", sa.String(200), nullable=False),
        sa.Column("quota_exempt", sa.Boolean(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'reserved'"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(request_key) BETWEEN 1 AND 200 AND request_key ~ '[^[:space:]]'",
            name=op.f("ck_token_reservations_request_key_length"),
        ),
        sa.CheckConstraint(
            "quota_exempt = (budget_id IS NULL)",
            name=op.f("ck_token_reservations_exemption_budget"),
        ),
        sa.CheckConstraint(
            "reserved_tokens > 0", name=op.f("ck_token_reservations_reserved_tokens_positive")
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_token_reservations_input_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_token_reservations_output_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released')",
            name=op.f("ck_token_reservations_status"),
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND input_tokens IS NULL AND output_tokens IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'settled' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND CAST(input_tokens AS NUMERIC) + CAST(output_tokens AS NUMERIC) "
            "<= reserved_tokens) "
            "OR (status = 'released' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
            "AND input_tokens = 0 AND output_tokens = 0 AND completed_at IS NOT NULL)",
            name=op.f("ck_token_reservations_completion_state"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_token_reservations_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["budget_id", "user_id"],
            ["token_budgets.id", "token_budgets.user_id"],
            name=op.f("fk_token_reservations_budget_id_token_budgets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_reservations")),
        sa.UniqueConstraint("user_id", "request_key", name=op.f("uq_token_reservations_user_id")),
    )
    op.create_index("ix_token_reservations_budget_id", "token_reservations", ["budget_id"])
    op.create_index("ix_token_reservations_status", "token_reservations", ["status"])
    op.create_index(
        "ix_token_reservations_user_created_at", "token_reservations", ["user_id", "created_at"]
    )


def downgrade() -> None:
    # 새 한도 이력과 서비스 역할만 제거하며 기존 사용자·작업 공간·대화 데이터는 보존한다.
    op.drop_index("ix_token_reservations_user_created_at", table_name="token_reservations")
    op.drop_index("ix_token_reservations_status", table_name="token_reservations")
    op.drop_index("ix_token_reservations_budget_id", table_name="token_reservations")
    op.drop_table("token_reservations")
    op.drop_index("ix_token_budgets_user_period", table_name="token_budgets")
    op.drop_index("ix_token_budgets_plan_id", table_name="token_budgets")
    op.drop_table("token_budgets")
    op.drop_table("usage_plans")
    op.drop_constraint(op.f("ck_users_platform_role"), "users", type_="check")
    op.drop_column("users", "platform_role")
