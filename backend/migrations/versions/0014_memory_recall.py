"""개인 기억과 파생 문맥의 변경 세대, 원문 회수 출처를 추가한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_memory_recall"
down_revision = "0013_generation_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("memory_revision", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.create_check_constraint(op.f("ck_users_memory_revision"), "users", "memory_revision >= 0")
    for table in ("generation_runs", "conversation_compactions"):
        op.add_column(
            table,
            sa.Column(
                "memory_dependencies",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_memory_dependencies"),
            table,
            "jsonb_typeof(memory_dependencies) = 'object'",
        )
    op.add_column(
        "generation_runs",
        sa.Column(
            "recall_sources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_generation_runs_recall_sources"),
        "generation_runs",
        "jsonb_typeof(recall_sources) = 'array'",
    )
    op.create_table(
        "user_memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("content", sa.String(500), nullable=False),
        sa.UniqueConstraint("user_id", "key", name=op.f("uq_user_memories_user_id")),
        sa.CheckConstraint(
            "char_length(btrim(key)) BETWEEN 1 AND 80", name=op.f("ck_user_memories_key_length")
        ),
        sa.CheckConstraint(
            "char_length(btrim(content)) BETWEEN 1 AND 500",
            name=op.f("ck_user_memories_content_length"),
        ),
    )


def downgrade() -> None:
    # 세대 검사를 제거하면 삭제한 기억이 옛 답변·요약을 통해 재사용될 수 있다.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM users WHERE memory_revision > 0) "
            "OR EXISTS (SELECT 1 FROM generation_runs WHERE recall_sources <> '[]'::jsonb)"
        )
    ):
        raise RuntimeError(
            "기억 또는 원문 회수 이력이 있어 자동 롤백할 수 없습니다. "
            "백업과 파생 문맥 보존 정책을 먼저 확인하세요."
        )
    op.drop_table("user_memories")
    op.drop_constraint(op.f("ck_generation_runs_recall_sources"), "generation_runs", type_="check")
    op.drop_column("generation_runs", "recall_sources")
    for table in ("generation_runs", "conversation_compactions"):
        op.drop_constraint(op.f(f"ck_{table}_memory_dependencies"), table, type_="check")
        op.drop_column(table, "memory_dependencies")
    op.drop_constraint(op.f("ck_users_memory_revision"), "users", type_="check")
    op.drop_column("users", "memory_revision")
