"""대화·사용자 경계를 넘는 실행 연결을 DB에서 차단하고 정상 이력을 보존한다."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.db import Database
from backend.app.models import (
    Conversation,
    ConversationCompaction,
    GenerationRun,
    Message,
    TokenReservation,
    User,
)
from backend.tests.test_models import StoredCompactionRows, StoredRows
from backend.tests.test_models import compaction_rows as compaction_rows
from backend.tests.test_models import stored as stored

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def saved_history(
    connection: AsyncConnection, rows: StoredCompactionRows
) -> tuple[dict, dict, list[dict], dict]:
    """실패한 연결 변경이 원문·실행 옵션·회계·완료 요약을 바꾸지 않았는지 비교한다."""
    generation = (
        (
            await connection.execute(
                select(GenerationRun.__table__).where(GenerationRun.id == rows.generation)
            )
        )
        .mappings()
        .one()
    )
    reservation = (
        (
            await connection.execute(
                select(TokenReservation.__table__).where(
                    TokenReservation.id == generation["reservation_id"]
                )
            )
        )
        .mappings()
        .one()
    )
    messages = (
        (
            await connection.execute(
                select(Message.__table__)
                .where(Message.conversation_id == generation["conversation_id"])
                .order_by(Message.sequence)
            )
        )
        .mappings()
        .all()
    )
    compaction = (
        (
            await connection.execute(
                select(ConversationCompaction.__table__).where(
                    ConversationCompaction.id == rows.compaction
                )
            )
        )
        .mappings()
        .one()
    )
    return dict(generation), dict(reservation), [dict(row) for row in messages], dict(compaction)


async def other_conversation(connection: AsyncConnection, rows: StoredRows) -> UUID:
    """작업 공간은 같게 유지해 대화 식별자 경계만 독립적으로 검증한다."""
    return await connection.scalar(
        insert(Conversation)
        .values(
            workspace_id=rows.workspace,
            created_by=rows.user,
            title="다른 대화",
            model="test-model",
        )
        .returning(Conversation.id)
    )


@pytest.mark.parametrize(
    "field,role,constraint",
    [
        ("user_message_id", "user", "fk_generation_runs_user_message_scope"),
        ("assistant_message_id", "assistant", "fk_generation_runs_assistant_message_scope"),
    ],
)
async def test_generation_rejects_messages_from_another_conversation(
    schema_database: Database,
    stored: StoredRows,
    compaction_rows: StoredCompactionRows,
    field: str,
    role: str,
    constraint: str,
) -> None:
    async with schema_database.engine.begin() as connection:
        foreign_conversation = await other_conversation(connection, stored)
        foreign_message = await connection.scalar(
            insert(Message)
            .values(
                workspace_id=stored.workspace,
                conversation_id=foreign_conversation,
                sequence=1,
                role=role,
                created_by=stored.user if role == "user" else None,
                content="다른 대화의 원문",
            )
            .returning(Message.id)
        )
        before = await saved_history(connection, compaction_rows)
        with pytest.raises(IntegrityError) as error:
            async with connection.begin_nested():
                await connection.execute(
                    update(GenerationRun)
                    .where(GenerationRun.id == compaction_rows.generation)
                    .values({field: foreign_message})
                )
        assert constraint in str(error.value.orig)
        assert await saved_history(connection, compaction_rows) == before


async def test_generation_rejects_another_users_reservation(
    schema_database: Database, compaction_rows: StoredCompactionRows
) -> None:
    async with schema_database.engine.begin() as connection:
        foreign_user = await connection.scalar(
            insert(User)
            .values(email=f"scope-{uuid4().hex}@example.com", display_name="다른 사용자")
            .returning(User.id)
        )
        foreign_reservation = await connection.scalar(
            insert(TokenReservation)
            .values(
                user_id=foreign_user,
                request_key="another-users-reservation",
                quota_exempt=True,
                reserved_tokens=1024,
            )
            .returning(TokenReservation.id)
        )
        before = await saved_history(connection, compaction_rows)
        with pytest.raises(IntegrityError) as error:
            async with connection.begin_nested():
                await connection.execute(
                    update(GenerationRun)
                    .where(GenerationRun.id == compaction_rows.generation)
                    .values(reservation_id=foreign_reservation)
                )
        assert "fk_generation_runs_reservation_user" in str(error.value.orig)
        assert await saved_history(connection, compaction_rows) == before


async def test_compaction_rejects_generation_from_another_conversation(
    schema_database: Database, stored: StoredRows, compaction_rows: StoredCompactionRows
) -> None:
    async with schema_database.engine.begin() as connection:
        foreign_conversation = await other_conversation(connection, stored)
        before = await saved_history(connection, compaction_rows)
        with pytest.raises(IntegrityError) as error:
            async with connection.begin_nested():
                await connection.execute(
                    update(ConversationCompaction)
                    .where(ConversationCompaction.id == compaction_rows.compaction)
                    .values(conversation_id=foreign_conversation)
                )
        assert "fk_conversation_compactions_generation_scope" in str(error.value.orig)
        assert await saved_history(connection, compaction_rows) == before
