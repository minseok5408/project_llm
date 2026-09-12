"""역할별 스키마 정리의 왕복 보존과 자동 변환을 거부해야 하는 기존 자료를 검증한다."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text as sql

from backend.tests.conftest import run_alembic, version_rows

pytestmark = pytest.mark.postgres

OLD_REVISION = "0016_local_file_rag"
NEW_REVISION = "0017_schema_roles"
BUSINESS_TABLES = (
    "users",
    "workspaces",
    "workspace_members",
    "conversations",
    "messages",
    "token_reservations",
    "generation_runs",
    "conversation_compactions",
    "documents",
    "document_versions",
    "chunks",
)
REMOVED_COLUMNS = {
    "conversations": ("settings",),
    "messages": ("prompt_version",),
    "generation_runs": ("options", "thinking"),
    "document_versions": ("media_type",),
}


@pytest.fixture
async def legacy_records(postgres, database):
    """현재 ORM과 분리된 SQL로 구 스키마의 정상 업무 이력을 구성한다."""
    await run_alembic(postgres, "upgrade", OLD_REVISION)
    values = {
        key: uuid4()
        for key in (
            "user_id",
            "workspace_id",
            "conversation_id",
            "user_message_id",
            "assistant_message_id",
            "reservation_id",
            "generation_id",
            "idempotency_key",
            "document_id",
            "deleted_document_id",
            "version_id",
            "deleted_version_id",
            "chunk_id",
        )
    }
    content = "보존할 휴가 정책: 연차는 15일입니다."
    values.update(
        content=content,
        content_length=len(content),
        byte_size=len(content.encode()),
        dependencies=json.dumps({f"document:{values['document_id']}": 3}),
        sources=json.dumps(
            [
                {
                    "document_id": str(values["document_id"]),
                    "chunk_id": str(values["chunk_id"]),
                    "filename": "보존할 정책.md",
                    "page": 1,
                    "chunk": 1,
                    "start": 0,
                    "end": len(content),
                }
            ],
            ensure_ascii=False,
        ),
    )
    statements = (
        "INSERT INTO users (id, email, display_name, platform_role) "
        "VALUES (:user_id, 'schema-test@example.com', '스키마 검증 사용자', 'system')",
        "INSERT INTO workspaces (id, name, created_by) "
        "VALUES (:workspace_id, '보존할 작업 공간', :user_id)",
        "INSERT INTO workspace_members (workspace_id, user_id, role) "
        "VALUES (:workspace_id, :user_id, 'owner')",
        "INSERT INTO conversations (id, workspace_id, created_by, title, model, "
        "next_message_sequence) VALUES "
        "(:conversation_id, :workspace_id, :user_id, '보존할 대화', 'test-model', 3)",
        "INSERT INTO messages (id, workspace_id, conversation_id, sequence, role, content, "
        "created_by, token_count, model) VALUES "
        "(:user_message_id, :workspace_id, :conversation_id, 1, 'user', "
        "'휴가 정책을 설명해 주세요.', :user_id, 31, NULL), "
        "(:assistant_message_id, :workspace_id, :conversation_id, 2, 'assistant', "
        "'연차는 15일입니다. [파일 1]', NULL, 17, 'test-model')",
        "INSERT INTO token_reservations (id, user_id, request_key, quota_exempt, "
        "reserved_tokens, input_tokens, output_tokens, status, completed_at, usage_basis, "
        "charge_mode) VALUES (:reservation_id, :user_id, 'schema-test-generation', true, "
        "159, 31, 17, 'settled', now(), 'provider', 'deferred')",
        "INSERT INTO generation_runs (id, workspace_id, conversation_id, user_id, "
        "user_message_id, assistant_message_id, reservation_id, idempotency_key, request_hash, "
        "request_messages, options, prompt_tokens, max_output_tokens, status, completed_at, "
        "file_sources, memory_dependencies) VALUES (:generation_id, :workspace_id, "
        ":conversation_id, :user_id, :user_message_id, :assistant_message_id, :reservation_id, "
        ":idempotency_key, repeat('a', 64), '[]', '{\"thinking\": true, \"max_tokens\": 128}', "
        "31, 128, 'completed', now(), CAST(:sources AS jsonb), CAST(:dependencies AS jsonb))",
        "INSERT INTO conversation_compactions (workspace_id, conversation_id, generation_id, "
        "model, through_sequence, content, status, input_tokens, output_tokens, usage_basis, "
        "memory_dependencies) VALUES (:workspace_id, :conversation_id, :generation_id, "
        "'test-model', 2, '휴가 규정을 함께 확인한 대화', 'completed', 40, 12, 'provider', "
        "CAST(:dependencies AS jsonb))",
        "INSERT INTO documents (id, workspace_id, conversation_id, uploaded_by, filename, "
        "deleted_at) VALUES (:document_id, :workspace_id, :conversation_id, :user_id, "
        "'보존할 정책.md', NULL), (:deleted_document_id, :workspace_id, :conversation_id, "
        ":user_id, '삭제된 파일', now())",
        "INSERT INTO document_versions (id, document_id, version, sha256, byte_size, media_type, "
        "extension, status, parser_version, embedding_version, scan_engine, page_count, "
        "chunk_count) VALUES (:version_id, :document_id, 3, repeat('b', 64), :byte_size, "
        "'text/plain', '.md', 'ready', 'test-parser-v1', 'test-embedding-v1', 'test-scan', 1, 1), "
        "(:deleted_version_id, :deleted_document_id, 1, repeat('c', 64), 16, 'application/pdf', "
        "'.pdf', 'uploaded', NULL, NULL, NULL, NULL, NULL)",
        "INSERT INTO chunks (id, version_id, ordinal, page, start_char, end_char, content, "
        "embedding) VALUES (:chunk_id, :version_id, 1, 1, 0, :content_length, :content, "
        "ARRAY[1.0::real] || array_fill(0.0::real, ARRAY[383]))",
        "INSERT INTO attachments (workspace_id, conversation_id, document_id) "
        "VALUES (:workspace_id, :conversation_id, :document_id)",
    )
    async with database.engine.begin() as connection:
        for statement in statements:
            await connection.execute(sql(statement), values)
    return values


async def snapshot(database, *, full_legacy=False):
    """제거 대상 이외의 모든 열을 비교해 원문·사용량·출처와 식별자 변경을 잡는다."""
    tables = (*BUSINESS_TABLES, "attachments") if full_legacy else BUSINESS_TABLES
    result = {}
    async with database.engine.connect() as connection:
        for table in tables:
            removed = () if full_legacy else REMOVED_COLUMNS.get(table, ())
            expression = "to_jsonb(item)" + "".join(f" - '{column}'" for column in removed)
            # 테이블과 열 이름은 이 파일 안의 고정 목록에서만 가져온다.
            result[table] = list(
                await connection.scalars(
                    sql(f"SELECT {expression} FROM {table} AS item ORDER BY id")
                )
            )
    return result


async def assert_upgraded(database, preserved):
    assert await version_rows(database) == [NEW_REVISION]
    assert await snapshot(database) == preserved
    async with database.engine.connect() as connection:
        assert (
            await connection.execute(sql("SELECT thinking, max_output_tokens FROM generation_runs"))
        ).one() == (True, 128)
        assert await connection.scalar(sql("SELECT to_regclass('public.attachments')")) is None
        columns = set(
            await connection.scalars(
                sql(
                    "SELECT table_name || '.' || column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
            )
        )
        assert not columns & {
            "conversations.settings",
            "messages.prompt_version",
            "generation_runs.options",
            "document_versions.media_type",
        }


async def test_schema_roles_round_trip_preserves_originals_usage_and_file_sources(
    postgres, database, legacy_records
):
    preserved = await snapshot(database)
    await run_alembic(postgres, "upgrade", NEW_REVISION)
    await assert_upgraded(database, preserved)
    await run_alembic(postgres, "check")

    await run_alembic(postgres, "downgrade", OLD_REVISION)
    assert await version_rows(database) == [OLD_REVISION]
    assert await snapshot(database) == preserved
    async with database.engine.connect() as connection:
        assert await connection.scalar(sql("SELECT options FROM generation_runs")) == {
            "thinking": True,
            "max_tokens": 128,
        }
        assert await connection.scalar(sql("SELECT settings FROM conversations")) == {}
        assert list(await connection.scalars(sql("SELECT prompt_version FROM messages"))) == [
            None,
            None,
        ]
        assert dict(
            (
                await connection.execute(sql("SELECT extension, media_type FROM document_versions"))
            ).all()
        ) == {".md": "text/plain", ".pdf": "application/pdf"}
        attachments = (
            await connection.execute(
                sql("SELECT workspace_id, conversation_id, document_id FROM attachments")
            )
        ).all()
        # 삭제 대기 문서는 원본 버전이 남아 있어도 활성 첨부로 되살리지 않는다.
        assert attachments == [
            (
                legacy_records["workspace_id"],
                legacy_records["conversation_id"],
                legacy_records["document_id"],
            )
        ]

    await run_alembic(postgres, "upgrade", NEW_REVISION)
    await assert_upgraded(database, preserved)
    await run_alembic(postgres, "check")


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            'UPDATE conversations SET settings = \'{"custom": "보존할 설정"}\'',
            id="nonempty-settings",
        ),
        pytest.param(
            "UPDATE messages SET prompt_version = 'custom-v1' WHERE role = 'assistant'",
            id="prompt-history",
        ),
        pytest.param(
            "UPDATE generation_runs SET options = options || '{\"temperature\": 0.7}'",
            id="unknown-option",
        ),
        pytest.param(
            "UPDATE generation_runs SET options = options || '{\"max_tokens\": 64}'",
            id="different-output-limit",
        ),
        pytest.param("DELETE FROM attachments", id="missing-active-attachment"),
        pytest.param(
            "INSERT INTO document_versions (document_id, version, sha256, byte_size, media_type, "
            "extension) SELECT document_id, 4, sha256, byte_size, media_type, extension "
            "FROM document_versions WHERE version = 3",
            id="multiple-document-versions",
        ),
        pytest.param(
            "UPDATE document_versions SET media_type = 'application/custom' WHERE version = 3",
            id="nonreconstructible-media-type",
        ),
    ],
)
async def test_schema_roles_rejects_lossy_conversion_without_mutating_legacy_data(
    postgres, database, legacy_records, mutation
):
    async with database.engine.begin() as connection:
        await connection.execute(sql(mutation))
    preserved = await snapshot(database, full_legacy=True)
    await run_alembic(postgres, "upgrade", NEW_REVISION, success=False)
    assert await version_rows(database) == [OLD_REVISION]
    assert await snapshot(database, full_legacy=True) == preserved
