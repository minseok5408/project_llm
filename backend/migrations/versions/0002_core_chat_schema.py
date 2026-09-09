"""사용자, 작업 공간, 소속 관계, 대화, 메시지의 초기 스키마를 추가한다.

리비전 ID: 0002_core_chat_schema
이전 리비전: 0001_database_baseline
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_core_chat_schema"
down_revision: str | Sequence[str] | None = "0001_database_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def identity_columns() -> list[sa.Column]:
    """각 테이블에 독립된 UUID·시각 컬럼을 생성한다. 현재 ORM 정의에는 의존하지 않는다."""
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
    op.create_table(
        "users",
        *identity_columns(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(btrim(email))", name=op.f("ck_users_email_normalized")),
        sa.CheckConstraint(
            "char_length(email) BETWEEN 3 AND 320", name=op.f("ck_users_email_length")
        ),
        sa.CheckConstraint(
            r"email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'",
            name=op.f("ck_users_email_format"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 200",
            name=op.f("ck_users_display_name_length"),
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_users_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "workspaces",
        *identity_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 200", name=op.f("ck_workspaces_name_length")
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_workspaces_status")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_workspaces_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_index("ix_workspaces_created_by", "workspaces", ["created_by"])

    op.create_table(
        "workspace_members",
        *identity_columns(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name=op.f("ck_workspace_members_role")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_members")),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name=op.f("uq_workspace_members_workspace_id")
        ),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    op.create_table(
        "conversations",
        *identity_columns(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column(
            "settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_message_sequence", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 300",
            name=op.f("ck_conversations_title_length"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_conversations_status")
        ),
        sa.CheckConstraint(
            "char_length(btrim(model)) BETWEEN 1 AND 255",
            name=op.f("ck_conversations_model_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(settings) = 'object'", name=op.f("ck_conversations_settings_object")
        ),
        sa.CheckConstraint(
            "next_message_sequence > 0",
            name=op.f("ck_conversations_next_message_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_conversations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_conversations_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_conversations_workspace_id")),
    )
    op.create_index("ix_conversations_created_by", "conversations", ["created_by"])
    op.create_index(
        "ix_conversations_workspace_status_cursor",
        "conversations",
        ["workspace_id", "status", sa.text("last_message_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "messages",
        *identity_columns(),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'completed'"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_count", sa.BigInteger(), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("prompt_version", sa.String(255), nullable=True),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_messages_sequence_positive")),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_role")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_messages_status"),
        ),
        sa.CheckConstraint(
            "(role = 'user' AND created_by IS NOT NULL AND status = 'completed') "
            "OR (role IN ('assistant', 'system') AND created_by IS NULL)",
            name=op.f("ck_messages_role_author_status"),
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR content ~ '[^[:space:]]'",
            name=op.f("ck_messages_completed_content"),
        ),
        sa.CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name=op.f("ck_messages_token_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "model IS NULL OR char_length(btrim(model)) BETWEEN 1 AND 255",
            name=op.f("ck_messages_model_length"),
        ),
        sa.CheckConstraint(
            "prompt_version IS NULL OR char_length(btrim(prompt_version)) BETWEEN 1 AND 255",
            name=op.f("ck_messages_prompt_version_length"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
            name=op.f("fk_messages_workspace_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_messages_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name=op.f("uq_messages_conversation_id")
        ),
    )
    op.create_index(
        "ix_messages_workspace_id_conversation_id", "messages", ["workspace_id", "conversation_id"]
    )
    op.create_index("ix_messages_created_by", "messages", ["created_by"])


def downgrade() -> None:
    # 참조하는 테이블부터 역순으로 제거하여 기존 기준 리비전으로 되돌린다.
    op.drop_index("ix_messages_created_by", table_name="messages")
    op.drop_index("ix_messages_workspace_id_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_workspace_status_cursor", table_name="conversations")
    op.drop_index("ix_conversations_created_by", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index("ix_workspaces_created_by", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("users")
