"""모델 호출별 사용량과 도구 실행 상태를 추가한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_generation_steps"
down_revision = "0012_network_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("call_id", sa.String(200)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("budget_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("usage_basis", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(60)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_generation_steps_sequence")),
        sa.CheckConstraint("kind IN ('llm','tool')", name=op.f("ck_generation_steps_kind")),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','cancelled')",
            name=op.f("ck_generation_steps_status"),
        ),
        sa.CheckConstraint(
            "usage_basis IN ('provider','received','waived')",
            name=op.f("ck_generation_steps_usage_basis"),
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND max_output_tokens >= 0 AND budget_tokens >= 0",
            name=op.f("ck_generation_steps_limits"),
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name=op.f("ck_generation_steps_usage")
        ),
        sa.CheckConstraint(
            "(status = 'running') = (completed_at IS NULL)",
            name=op.f("ck_generation_steps_completion"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generation_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_generation_steps_generation_id_generation_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_steps")),
        sa.UniqueConstraint(
            "generation_id", "sequence", name=op.f("uq_generation_steps_generation_id")
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM generation_steps)")):
        raise RuntimeError("실행·사용량 기록이 있으므로 먼저 보존 정책을 결정해야 합니다.")
    op.drop_table("generation_steps")
