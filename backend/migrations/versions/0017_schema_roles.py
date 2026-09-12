"""중복 첨부·미사용 메타데이터를 정리하고 생성 관계의 소속을 검증한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0017_schema_roles"
down_revision = "0016_local_file_rag"
branch_labels = None
depends_on = None


def reject_rows(query: str, reason: str) -> None:
    # 고정 SQL만 사용하며 오프라인 SQL 생성에서도 데이터 보존 검사를 유지한다.
    op.execute(f"DO $$ BEGIN IF EXISTS ({query}) THEN RAISE EXCEPTION '{reason}'; END IF; END $$")


def upgrade():
    # 검사와 구조 변경 사이에 쓰기가 끼어들지 않게 같은 트랜잭션에서 잠근다.
    op.execute(
        "LOCK TABLE conversations, messages, generation_runs, token_reservations, "
        "conversation_compactions, documents, document_versions, attachments "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    for query, reason in (
        (
            "SELECT 1 FROM generation_runs WHERE status IN ('queued','running')",
            "진행 중 생성이 있습니다. 작업 종료 후 스키마를 변경하세요.",
        ),
        (
            "SELECT 1 FROM conversations WHERE settings <> '{}'::jsonb",
            "사용자 지정 대화 설정이 있어 자동으로 제거할 수 없습니다.",
        ),
        (
            "SELECT 1 FROM messages WHERE prompt_version IS NOT NULL",
            "메시지 프롬프트 버전 이력이 있어 자동으로 제거할 수 없습니다.",
        ),
        (
            "SELECT 1 FROM generation_runs WHERE options - 'thinking' - 'max_tokens' "
            "<> '{}'::jsonb OR (options ? 'thinking' "
            "AND jsonb_typeof(options->'thinking') <> 'boolean') "
            "OR (options ? 'max_tokens' "
            "AND options->'max_tokens' IS DISTINCT FROM to_jsonb(max_output_tokens))",
            "알 수 없거나 서로 다른 생성 옵션이 있어 자동으로 정규화할 수 없습니다.",
        ),
        (
            "SELECT 1 FROM documents d WHERE (d.deleted_at IS NULL) <> "
            "EXISTS (SELECT 1 FROM attachments a WHERE a.document_id = d.id)",
            "문서와 활성 첨부 범위가 달라 자동으로 통합할 수 없습니다.",
        ),
        (
            "SELECT 1 FROM document_versions GROUP BY document_id HAVING count(*) > 1",
            "문서에 여러 버전이 있어 자동으로 단일 버전 제약을 적용할 수 없습니다.",
        ),
        (
            "SELECT 1 FROM document_versions WHERE media_type <> "
            "CASE WHEN extension = '.pdf' THEN 'application/pdf' ELSE 'text/plain' END",
            "재구성할 수 없는 문서 형식 이력이 있어 자동으로 제거할 수 없습니다.",
        ),
    ):
        reject_rows(query, reason)

    op.add_column(
        "generation_runs",
        sa.Column("thinking", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        "UPDATE generation_runs SET thinking = COALESCE((options->>'thinking')::boolean, false)"
    )
    op.drop_constraint(op.f("ck_generation_runs_options_object"), "generation_runs", type_="check")
    op.drop_column("generation_runs", "options")
    op.drop_constraint(op.f("ck_conversations_settings_object"), "conversations", type_="check")
    op.drop_column("conversations", "settings")
    op.drop_constraint(op.f("ck_messages_prompt_version_length"), "messages", type_="check")
    op.drop_column("messages", "prompt_version")
    op.drop_table("attachments")
    op.drop_constraint(op.f("uq_documents_workspace_id"), "documents", type_="unique")
    op.drop_column("document_versions", "media_type")
    op.drop_constraint(
        op.f("uq_document_versions_document_id"), "document_versions", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_document_versions_document_id"), "document_versions", ["document_id"]
    )

    op.create_unique_constraint(
        "uq_messages_conversation_identity", "messages", ["conversation_id", "id"]
    )
    op.create_unique_constraint(
        "uq_token_reservations_identity_user", "token_reservations", ["id", "user_id"]
    )
    op.create_unique_constraint(
        "uq_generation_runs_conversation_identity", "generation_runs", ["conversation_id", "id"]
    )
    for column, name in (
        ("user_message_id", "fk_generation_runs_user_message_scope"),
        ("assistant_message_id", "fk_generation_runs_assistant_message_scope"),
    ):
        op.drop_constraint(
            op.f(f"fk_generation_runs_{column}_messages"), "generation_runs", type_="foreignkey"
        )
        op.create_foreign_key(
            name,
            "generation_runs",
            "messages",
            ["conversation_id", column],
            ["conversation_id", "id"],
            ondelete="RESTRICT",
        )
    op.drop_constraint(
        op.f("fk_generation_runs_reservation_id_token_reservations"),
        "generation_runs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_generation_runs_reservation_user",
        "generation_runs",
        "token_reservations",
        ["reservation_id", "user_id"],
        ["id", "user_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("fk_conversation_compactions_generation_id_generation_runs"),
        "conversation_compactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_conversation_compactions_generation_scope",
        "conversation_compactions",
        "generation_runs",
        ["conversation_id", "generation_id"],
        ["conversation_id", "id"],
        ondelete="CASCADE",
    )


def downgrade():
    # 되돌리기에서도 검사 직후 새 작업이 등록되지 않도록 쓰기를 먼저 막는다.
    op.execute(
        "LOCK TABLE conversations, messages, generation_runs, token_reservations, "
        "conversation_compactions, documents, document_versions "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    reject_rows(
        "SELECT 1 FROM generation_runs WHERE status IN ('queued','running')",
        "진행 중 생성이 있습니다. 작업 종료 후 스키마를 되돌리세요.",
    )
    for table, name, target, column, ondelete in (
        (
            "conversation_compactions",
            "fk_conversation_compactions_generation_scope",
            "generation_runs",
            "generation_id",
            "CASCADE",
        ),
        (
            "generation_runs",
            "fk_generation_runs_reservation_user",
            "token_reservations",
            "reservation_id",
            "RESTRICT",
        ),
        (
            "generation_runs",
            "fk_generation_runs_user_message_scope",
            "messages",
            "user_message_id",
            "RESTRICT",
        ),
        (
            "generation_runs",
            "fk_generation_runs_assistant_message_scope",
            "messages",
            "assistant_message_id",
            "RESTRICT",
        ),
    ):
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            op.f(f"fk_{table}_{column}_{target}"),
            table,
            target,
            [column],
            ["id"],
            ondelete=ondelete,
        )
    for table, name in (
        ("generation_runs", "uq_generation_runs_conversation_identity"),
        ("token_reservations", "uq_token_reservations_identity_user"),
        ("messages", "uq_messages_conversation_identity"),
    ):
        op.drop_constraint(name, table, type_="unique")

    op.add_column("generation_runs", sa.Column("options", pg.JSONB(), nullable=True))
    op.execute(
        "UPDATE generation_runs SET options = jsonb_build_object("
        "'thinking', thinking, 'max_tokens', max_output_tokens)"
    )
    op.alter_column("generation_runs", "options", nullable=False)
    op.create_check_constraint(
        op.f("ck_generation_runs_options_object"),
        "generation_runs",
        "jsonb_typeof(options) = 'object'",
    )
    op.drop_column("generation_runs", "thinking")
    op.add_column(
        "conversations",
        sa.Column("settings", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_check_constraint(
        op.f("ck_conversations_settings_object"),
        "conversations",
        "jsonb_typeof(settings) = 'object'",
    )
    op.add_column("messages", sa.Column("prompt_version", sa.String(255), nullable=True))
    op.create_check_constraint(
        op.f("ck_messages_prompt_version_length"),
        "messages",
        "prompt_version IS NULL OR char_length(btrim(prompt_version)) BETWEEN 1 AND 255",
    )
    op.add_column("document_versions", sa.Column("media_type", sa.String(80), nullable=True))
    op.execute(
        "UPDATE document_versions SET media_type = CASE WHEN extension = '.pdf' "
        "THEN 'application/pdf' ELSE 'text/plain' END"
    )
    op.alter_column("document_versions", "media_type", nullable=False)
    op.drop_constraint(
        op.f("uq_document_versions_document_id"), "document_versions", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_document_versions_document_id"), "document_versions", ["document_id", "version"]
    )
    op.create_unique_constraint(
        op.f("uq_documents_workspace_id"), "documents", ["workspace_id", "conversation_id", "id"]
    )
    op.create_table(
        "attachments",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id", "document_id"],
            ["documents.workspace_id", "documents.conversation_id", "documents.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id"),
    )
    # 연결에는 별도 업무 정보가 없으므로 활성 문서의 연결만 다시 구성한다.
    op.execute(
        "INSERT INTO attachments "
        "(workspace_id, conversation_id, document_id, created_at, updated_at) "
        "SELECT workspace_id, conversation_id, id, created_at, updated_at "
        "FROM documents WHERE deleted_at IS NULL"
    )
