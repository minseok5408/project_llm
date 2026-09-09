"""대화 요약과 사용자 한도에서 제외되는 압축 작업 사용량을 저장한다."""

import sqlalchemy as sa
from alembic import op

revision = "0009_context_compaction"
down_revision = "0008_deferred_charging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "context_compaction_needed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "conversation_compactions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("generation_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("through_sequence", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'running'")),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("usage_basis", sa.String(20), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            ondelete="CASCADE",
            name=op.f("fk_conversation_compactions_workspace_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generation_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_conversation_compactions_generation_id_generation_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_compactions")),
        sa.CheckConstraint(
            "model ~ '[^[:space:]]'", name=op.f("ck_conversation_compactions_model_nonblank")
        ),
        sa.CheckConstraint(
            "prompt_version > 0", name=op.f("ck_conversation_compactions_prompt_version_positive")
        ),
        sa.CheckConstraint(
            "through_sequence >= 0",
            name=op.f("ck_conversation_compactions_through_sequence_nonnegative"),
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','cancelled')",
            name=op.f("ck_conversation_compactions_status"),
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name=op.f("ck_conversation_compactions_input_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name=op.f("ck_conversation_compactions_output_tokens_nonnegative"),
        ),
        sa.CheckConstraint(
            "usage_basis IN ('provider','received')",
            name=op.f("ck_conversation_compactions_usage_basis"),
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL AND output_tokens IS NULL AND usage_basis IS NULL) OR "
            "(input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND usage_basis IS NOT NULL)",
            name=op.f("ck_conversation_compactions_usage_state"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND content IS NOT NULL AND content ~ '[^[:space:]]' "
            "AND through_sequence > 0 AND input_tokens IS NOT NULL "
            "AND output_tokens IS NOT NULL AND usage_basis IS NOT NULL) OR "
            "(status != 'completed' AND content IS NULL)",
            name=op.f("ck_conversation_compactions_content_state"),
        ),
    )
    op.create_index(
        "ix_conversation_compactions_lookup",
        "conversation_compactions",
        ["workspace_id", "conversation_id", "through_sequence"],
    )
    op.create_index(
        "ix_conversation_compactions_generation_id",
        "conversation_compactions",
        ["generation_id"],
    )


def downgrade() -> None:
    op.drop_table("conversation_compactions")
    op.drop_column("generation_runs", "context_compaction_needed")
