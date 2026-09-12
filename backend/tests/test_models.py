"""마이그레이션이 생성한 PostgreSQL 스키마의 제약조건과 삭제 정책 검증."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db import Database
from backend.app.models import ConversationCompaction, GenerationRun, Message, TokenReservation

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@dataclass
class StoredRows:
    user: UUID
    workspace: UUID
    membership: UUID
    conversation: UUID
    message: UUID


@dataclass
class StoredCompactionRows:
    generation: UUID
    compaction: UUID


@pytest.fixture
async def stored(schema_database: Database) -> StoredRows:
    async with schema_database.engine.begin() as connection:
        user = await connection.scalar(
            text(
                "INSERT INTO users (email, display_name) VALUES ('owner@example.com', '소유자') "
                "RETURNING id"
            )
        )
        workspace = await connection.scalar(
            text(
                "INSERT INTO workspaces (name, created_by) "
                "VALUES ('테스트 공간', :user) RETURNING id"
            ),
            {"user": user},
        )
        membership = await connection.scalar(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:workspace, :user, 'owner') RETURNING id"
            ),
            {"workspace": workspace, "user": user},
        )
        conversation = await connection.scalar(
            text(
                "INSERT INTO conversations (workspace_id, created_by, title, model) "
                "VALUES (:workspace, :user, '기본 대화', 'test-model') RETURNING id"
            ),
            {"workspace": workspace, "user": user},
        )
        message = await connection.scalar(
            text(
                "INSERT INTO messages "
                "(workspace_id, conversation_id, sequence, role, content, created_by) "
                "VALUES (:workspace, :conversation, 1, 'user', '한글 메시지', :user) RETURNING id"
            ),
            {"workspace": workspace, "conversation": conversation, "user": user},
        )
    return StoredRows(user, workspace, membership, conversation, message)


@pytest.fixture
async def compaction_rows(schema_database: Database, stored: StoredRows) -> StoredCompactionRows:
    async with schema_database.session() as session:
        assistant = Message(
            workspace_id=stored.workspace,
            conversation_id=stored.conversation,
            sequence=2,
            role="assistant",
            content="",
            status="pending",
        )
        reservation = TokenReservation(
            user_id=stored.user,
            request_key="compaction-schema-test",
            quota_exempt=True,
            reserved_tokens=1024,
        )
        session.add_all([assistant, reservation])
        await session.flush()
        generation = GenerationRun(
            workspace_id=stored.workspace,
            conversation_id=stored.conversation,
            user_id=stored.user,
            user_message_id=stored.message,
            assistant_message_id=assistant.id,
            reservation_id=reservation.id,
            idempotency_key=uuid4(),
            request_hash="a" * 64,
            request_messages=[],
            thinking=False,
            prompt_tokens=0,
            max_output_tokens=1024,
        )
        session.add(generation)
        await session.flush()
        compaction = ConversationCompaction(
            workspace_id=stored.workspace,
            conversation_id=stored.conversation,
            generation_id=generation.id,
            model="test-model",
            through_sequence=2,
            status="completed",
            content="사용자는 한글로 대화한다.",
            input_tokens=100,
            output_tokens=20,
            usage_basis="provider",
        )
        session.add(compaction)
        await session.commit()
        return StoredCompactionRows(generation.id, compaction.id)


async def test_server_uuid_time_defaults_and_conversation_schema(
    schema_database: Database, stored: StoredRows
) -> None:
    identifiers = {
        "users": stored.user,
        "workspaces": stored.workspace,
        "workspace_members": stored.membership,
        "conversations": stored.conversation,
        "messages": stored.message,
    }
    async with schema_database.session() as session:
        columns = (
            (
                await session.execute(
                    text(
                        "SELECT table_name, column_name, data_type, column_default "
                        "FROM information_schema.columns WHERE table_schema = 'public'"
                    )
                )
            )
            .mappings()
            .all()
        )
        by_column = {(row["table_name"], row["column_name"]): row for row in columns}
        for table, identifier in identifiers.items():
            assert isinstance(identifier, UUID)
            assert by_column[table, "id"]["data_type"] == "uuid"
            assert "gen_random_uuid" in by_column[table, "id"]["column_default"]
            row = (
                (
                    await session.execute(
                        text(f"SELECT * FROM {table} WHERE id = :id"), {"id": identifier}
                    )
                )
                .mappings()
                .one()
            )
            for name in ("created_at", "updated_at"):
                assert by_column[table, name]["data_type"] == "timestamp with time zone"
                assert row[name].utcoffset() == timedelta(0)
            if table in {"users", "workspaces", "conversations"}:
                assert row["status"] == "active"
        conversation = (
            (
                await session.execute(
                    text("SELECT * FROM conversations WHERE id = :id"), {"id": stored.conversation}
                )
            )
            .mappings()
            .one()
        )
        assert ("conversations", "settings") not in by_column
        assert ("messages", "prompt_version") not in by_column
        assert conversation["is_pinned"] is False
        assert conversation["last_message_at"].utcoffset() == timedelta(0)
        assert conversation["deleted_at"] is None
        assert conversation["next_message_sequence"] == 1
        assert (
            await session.scalar(
                text("SELECT status FROM messages WHERE id = :id"), {"id": stored.message}
            )
            == "completed"
        )


async def test_database_constraints_reject_invalid_rows(
    schema_database: Database, stored: StoredRows
) -> None:
    parameters = {
        "user": stored.user,
        "workspace": stored.workspace,
        "conversation": stored.conversation,
        "message": stored.message,
    }
    # 각 SAVEPOINT를 독립적으로 되돌려 하나의 실패가 이후 제약조건 검증을 가리지 않게 한다.
    invalid_statements = [
        "UPDATE users SET email = ' Mixed@Example.COM ' WHERE id = :user",
        "UPDATE users SET email = 'not-an-email' WHERE id = :user",
        "INSERT INTO users (email, display_name) VALUES ('owner@example.com', '중복')",
        "UPDATE users SET display_name = ' ' WHERE id = :user",
        "UPDATE users SET status = 'unknown' WHERE id = :user",
        "UPDATE workspaces SET name = ' ' WHERE id = :workspace",
        "UPDATE workspaces SET status = 'unknown' WHERE id = :workspace",
        "UPDATE workspace_members SET role = 'unknown' WHERE workspace_id = :workspace",
        "INSERT INTO workspace_members (workspace_id, user_id, role) "
        "VALUES (:workspace, :user, 'member')",
        "UPDATE conversations SET title = ' ' WHERE id = :conversation",
        "UPDATE conversations SET model = '' WHERE id = :conversation",
        "UPDATE conversations SET model = NULL WHERE id = :conversation",
        "UPDATE conversations SET status = 'unknown' WHERE id = :conversation",
        "UPDATE conversations SET next_message_sequence = 0 WHERE id = :conversation",
        "UPDATE messages SET sequence = 0 WHERE id = :message",
        "UPDATE messages SET role = 'unknown' WHERE id = :message",
        "UPDATE messages SET status = 'unknown' WHERE id = :message",
        "UPDATE messages SET token_count = -1 WHERE id = :message",
        "UPDATE messages SET created_by = NULL WHERE id = :message",
        "UPDATE messages SET status = 'pending' WHERE id = :message",
        "UPDATE messages SET role = 'assistant' WHERE id = :message",
        "UPDATE messages SET content = '' WHERE id = :message",
        "UPDATE messages SET content = E' \\n\\t ' WHERE id = :message",
        "INSERT INTO messages (workspace_id, conversation_id, sequence, role, content, created_by) "
        "VALUES (:workspace, :conversation, 1, 'user', '중복 순번', :user)",
    ]
    async with schema_database.engine.begin() as connection:
        for statement in invalid_statements:
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text(statement), parameters)


async def test_message_workspace_must_match_conversation(
    schema_database: Database, stored: StoredRows
) -> None:
    async with schema_database.engine.begin() as connection:
        other_workspace = await connection.scalar(
            text(
                "INSERT INTO workspaces (name, created_by) VALUES ('다른 공간', :user) RETURNING id"
            ),
            {"user": stored.user},
        )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    text(
                        "INSERT INTO messages "
                        "(workspace_id, conversation_id, sequence, role, content) "
                        "VALUES (:workspace, :conversation, 2, 'assistant', '잘못된 경계')"
                    ),
                    {"workspace": other_workspace, "conversation": stored.conversation},
                )


async def test_foreign_key_delete_policies(schema_database: Database, stored: StoredRows) -> None:
    async with schema_database.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    text("DELETE FROM users WHERE id = :user"), {"user": stored.user}
                )
        guest = await connection.scalar(
            text(
                "INSERT INTO users (email, display_name) "
                "VALUES ('guest@example.com', '참여자') RETURNING id"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:workspace, :user, 'member')"
            ),
            {"workspace": stored.workspace, "user": guest},
        )
        await connection.execute(text("DELETE FROM users WHERE id = :user"), {"user": guest})
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM workspace_members WHERE user_id = :user"),
                {"user": guest},
            )
            == 0
        )
        await connection.execute(
            text("DELETE FROM conversations WHERE id = :conversation"),
            {"conversation": stored.conversation},
        )
        assert await connection.scalar(text("SELECT count(*) FROM messages")) == 0
        conversation = await connection.scalar(
            text(
                "INSERT INTO conversations (workspace_id, created_by, title, model) "
                "VALUES (:workspace, :user, '삭제 대상', 'test-model') RETURNING id"
            ),
            {"workspace": stored.workspace, "user": stored.user},
        )
        await connection.execute(
            text(
                "INSERT INTO messages (workspace_id, conversation_id, sequence, role, content) "
                "VALUES (:workspace, :conversation, 1, 'assistant', '함께 삭제')"
            ),
            {"workspace": stored.workspace, "conversation": conversation},
        )
        await connection.execute(
            text("DELETE FROM workspaces WHERE id = :workspace"), {"workspace": stored.workspace}
        )
        for table in ("workspace_members", "conversations", "messages"):
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM users")) == 1


async def test_compaction_defaults_multiple_batches_and_generation_delete(
    schema_database: Database,
    stored: StoredRows,
    compaction_rows: StoredCompactionRows,
) -> None:
    async with schema_database.session() as session:
        generation = await session.get(GenerationRun, compaction_rows.generation)
        assert generation.context_compaction_needed is False
        completed = await session.get(ConversationCompaction, compaction_rows.compaction)
        assert completed.prompt_version == 1
        assert completed.created_at.utcoffset() == timedelta(0)
        assert completed.updated_at.utcoffset() == timedelta(0)
        pending = (
            (
                await session.execute(
                    text(
                        "INSERT INTO conversation_compactions "
                        "(workspace_id, conversation_id, generation_id, model, through_sequence) "
                        "VALUES (:workspace, :conversation, :generation, 'test-model', 4) "
                        "RETURNING id, status, prompt_version, content, input_tokens, "
                        "output_tokens, usage_basis"
                    ),
                    {
                        "workspace": stored.workspace,
                        "conversation": stored.conversation,
                        "generation": compaction_rows.generation,
                    },
                )
            )
            .mappings()
            .one()
        )
        assert isinstance(pending["id"], UUID)
        assert pending["status"] == "running"
        assert pending["prompt_version"] == 1
        assert all(
            pending[column] is None
            for column in ("content", "input_tokens", "output_tokens", "usage_basis")
        )
        assert await session.scalar(text("SELECT count(*) FROM conversation_compactions")) == 2
        await session.execute(
            text("DELETE FROM generation_runs WHERE id = :generation"),
            {"generation": compaction_rows.generation},
        )
        assert await session.scalar(text("SELECT count(*) FROM conversation_compactions")) == 0
        # 압축 기록을 삭제해도 원래 대화 메시지와 토큰 정산 기록은 유지한다.
        assert await session.scalar(text("SELECT count(*) FROM messages")) == 2
        assert await session.scalar(text("SELECT count(*) FROM token_reservations")) == 1


async def test_compaction_constraints_prevent_reusing_incomplete_summary(
    schema_database: Database, compaction_rows: StoredCompactionRows
) -> None:
    invalid_changes = [
        "model = E' \\n\\t '",
        "prompt_version = 0",
        "through_sequence = -1",
        "through_sequence = 0",
        "content = NULL",
        "content = E' \\n\\t '",
        "status = 'unknown'",
        "status = 'running'",
        "status = 'failed'",
        "status = 'cancelled'",
        "input_tokens = -1",
        "output_tokens = -1",
        "input_tokens = NULL",
        "output_tokens = NULL",
        "usage_basis = NULL",
        "usage_basis = 'waived'",
        "input_tokens = NULL, output_tokens = NULL, usage_basis = NULL",
    ]
    async with schema_database.engine.begin() as connection:
        for change in invalid_changes:
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(f"UPDATE conversation_compactions SET {change} WHERE id = :id"),
                        {"id": compaction_rows.compaction},
                    )
        # 실패·중단한 작업도 확인된 사용량은 유지하되 요약 내용은 재사용할 수 없게 비운다.
        await connection.execute(
            text(
                "UPDATE conversation_compactions SET status = 'cancelled', content = NULL, "
                "usage_basis = 'received' WHERE id = :id"
            ),
            {"id": compaction_rows.compaction},
        )
        await connection.execute(
            text(
                "UPDATE conversation_compactions SET status = 'failed', "
                "input_tokens = NULL, output_tokens = NULL, usage_basis = NULL WHERE id = :id"
            ),
            {"id": compaction_rows.compaction},
        )


async def test_compaction_workspace_must_match_conversation(
    schema_database: Database,
    stored: StoredRows,
    compaction_rows: StoredCompactionRows,
) -> None:
    async with schema_database.engine.begin() as connection:
        other_workspace = await connection.scalar(
            text(
                "INSERT INTO workspaces (name, created_by) VALUES ('다른 공간', :user) RETURNING id"
            ),
            {"user": stored.user},
        )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    text(
                        "UPDATE conversation_compactions SET workspace_id = :workspace "
                        "WHERE id = :id"
                    ),
                    {"workspace": other_workspace, "id": compaction_rows.compaction},
                )
