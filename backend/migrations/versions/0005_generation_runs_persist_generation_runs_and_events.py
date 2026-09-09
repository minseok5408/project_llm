"""생성 작업과 재연결 이벤트를 저장한다.

리비전 ID: 0005_generation_runs
이전 리비전: 0004_auth_sessions
생성 일시: 2026-09-09 10:59:19.451565
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_generation_runs"
down_revision: str | Sequence[str] | None = "0004_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 이 리비전의 작업·이벤트 구조를 명시적으로 적용한다.
    op.create_table(
        "generation_runs",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("user_message_id", sa.UUID(), nullable=False),
        sa.Column("assistant_message_id", sa.UUID(), nullable=False),
        sa.Column("reservation_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.UUID(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "last_event_sequence", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.CheckConstraint(
            "(status IN ('queued','running') AND completed_at IS NULL) OR "
            "(status IN ('completed','failed','cancelled','usage_pending') "
            "AND completed_at IS NOT NULL)",
            name=op.f("ck_generation_runs_completion_state"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(options) = 'object'", name=op.f("ck_generation_runs_options_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_messages) = 'array'",
            name=op.f("ck_generation_runs_messages_array"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_generation_runs_request_hash")
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','usage_pending')",
            name=op.f("ck_generation_runs_status"),
        ),
        sa.CheckConstraint(
            "last_event_sequence >= 0", name=op.f("ck_generation_runs_event_sequence")
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND max_output_tokens > 0",
            name=op.f("ck_generation_runs_reserved_usage"),
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["messages.id"],
            name=op.f("fk_generation_runs_assistant_message_id_messages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["token_reservations.id"],
            name=op.f("fk_generation_runs_reservation_id_token_reservations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_generation_runs_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["messages.id"],
            name=op.f("fk_generation_runs_user_message_id_messages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            name=op.f("fk_generation_runs_workspace_id_conversations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_runs")),
        sa.UniqueConstraint(
            "assistant_message_id", name=op.f("uq_generation_runs_assistant_message_id")
        ),
        sa.UniqueConstraint("reservation_id", name=op.f("uq_generation_runs_reservation_id")),
        sa.UniqueConstraint("user_id", "idempotency_key", name=op.f("uq_generation_runs_user_id")),
        sa.UniqueConstraint("user_message_id", name=op.f("uq_generation_runs_user_message_id")),
    )
    op.create_index(
        "ix_generation_runs_conversation",
        "generation_runs",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_generation_runs_queue",
        "generation_runs",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_generation_runs_active_conversation",
        "generation_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index(
        "uq_generation_runs_active_user",
        "generation_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_table(
        "generation_events",
        sa.Column("generation_id", sa.UUID(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name=op.f("ck_generation_events_payload_object")
        ),
        sa.CheckConstraint(
            "kind IN ('meta','delta','done','error','cancelled')",
            name=op.f("ck_generation_events_kind"),
        ),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_generation_events_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generation_runs.id"],
            name=op.f("fk_generation_events_generation_id_generation_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_events")),
        sa.UniqueConstraint(
            "generation_id", "sequence", name=op.f("uq_generation_events_generation_id")
        ),
    )


def downgrade() -> None:
    # 이 리비전의 작업·이벤트 구조를 명시적으로 적용한다.
    op.drop_table("generation_events")
    op.drop_index(
        "uq_generation_runs_active_user",
        table_name="generation_runs",
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.drop_index(
        "uq_generation_runs_active_conversation",
        table_name="generation_runs",
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.drop_index(
        "ix_generation_runs_queue",
        table_name="generation_runs",
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.drop_index("ix_generation_runs_conversation", table_name="generation_runs")
    op.drop_table("generation_runs")
