"""실제 PostgreSQL에서 저장 계층의 권한 경계와 트랜잭션 동작을 검증한다."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text, update

from backend.app.db import Database
from backend.app.models import Conversation, Message, User, Workspace, WorkspaceMember
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    Repository,
    create_user_with_workspace,
)
from backend.app.services.conversations import ConversationService
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def make_account(database: Database, email: str = "owner@example.com"):
    async with database.session() as session:
        account = await create_user_with_workspace(
            session, email=email, display_name="한글 사용자", workspace_name="나의 작업 공간"
        )
        await session.commit()
        return account


@pytest.fixture
async def account(schema_database: Database):
    return await make_account(schema_database)


async def test_user_workspace_and_owner_membership_are_atomic(schema_database: Database) -> None:
    def fail_membership(*_args) -> None:
        raise RuntimeError("membership insert failed")

    # 마지막 구성원 저장이 실패해도 먼저 INSERT한 사용자와 작업 공간이 남지 않아야 한다.
    event.listen(WorkspaceMember, "before_insert", fail_membership)
    try:
        with pytest.raises(RuntimeError, match="membership insert failed"):
            async with schema_database.session() as session:
                await create_user_with_workspace(
                    session, email="atomic@example.com", display_name="원자성 확인"
                )
    finally:
        event.remove(WorkspaceMember, "before_insert", fail_membership)

    async with schema_database.session() as session:
        for model in (User, Workspace, WorkspaceMember):
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    account = await make_account(schema_database, "  Mixed@Example.COM  ")
    assert account.user.email == "mixed@example.com"
    assert account.workspace.created_by == account.user.id
    assert account.membership.user_id == account.user.id
    assert account.membership.workspace_id == account.workspace.id
    assert account.membership.role == "owner"
    with pytest.raises(Conflict):
        async with schema_database.session() as session:
            await create_user_with_workspace(
                session, email="MIXED@example.com", display_name="중복 사용자"
            )
    async with schema_database.session() as session:
        for model in (User, Workspace, WorkspaceMember):
            assert await session.scalar(select(func.count()).select_from(model)) == 1


async def test_conversation_and_message_round_trip(schema_database: Database, account) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        conversation = await repository.create_conversation(
            account.workspace.id, title="한글 대화 제목", model="test-model"
        )
        first = await repository.append_message(
            account.workspace.id, conversation.id, role="user", content="한글 질문"
        )
        second = await repository.append_message(
            account.workspace.id,
            conversation.id,
            role="assistant",
            content="한글 응답 🌿",
            model="test-model",
            token_count=12,
        )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        assert (await repository.get_user()).id == account.user.id
        assert [row.id for row in await repository.list_workspaces()] == [account.workspace.id]
        assert (await repository.get_workspace(account.workspace.id)).name == "나의 작업 공간"
        loaded = await repository.get_conversation(account.workspace.id, conversation.id)
        assert loaded.title == "한글 대화 제목"
        assert loaded.model == "test-model"
        assert loaded.created_by == account.user.id
        assert loaded.last_message_at >= loaded.created_at
        assert loaded.next_message_sequence == 3
        page = await repository.list_messages(account.workspace.id, conversation.id)
        assert [row.content for row in page.items] == ["한글 질문", "한글 응답 🌿"]
        assert [row.id for row in page.items] == [first.id, second.id]
        assert [row.sequence for row in page.items] == [1, 2]
        assert page.items[0].created_by == account.user.id
        assert page.items[1].created_by is None
        assert page.items[1].model == "test-model"
        assert page.items[1].token_count == 12
        assert page.items[1].created_at.utcoffset() == timedelta(0)
        assert page.next_cursor is None


async def test_invalid_input_does_not_abort_or_mutate_transaction(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        with pytest.raises(InvalidInput):
            await create_user_with_workspace(
                session, email="person@localhost", display_name="잘못된 이메일"
            )
        repository = Repository(session, account.user.id)
        for invalid in [
            {"title": " "},
            {"model": ""},
            {"model": None},
        ]:
            with pytest.raises(InvalidInput):
                await repository.create_conversation(
                    account.workspace.id, **{"model": "test-model", **invalid}
                )
        conversation = await repository.create_conversation(
            account.workspace.id, model="test-model"
        )
        for invalid in [
            {"role": "unknown"},
            {"status": "unknown"},
            {"status": "pending"},
            {"content": " \n\t "},
            {"token_count": -1},
            {"token_count": True},
            {"model": " "},
        ]:
            with pytest.raises(InvalidInput):
                await repository.append_message(
                    account.workspace.id,
                    conversation.id,
                    **{"role": "user", "content": "검증 대상", **invalid},
                )
        saved = await repository.append_message(
            account.workspace.id, conversation.id, role="user", content="검증 후 정상 저장"
        )
        assert saved.sequence == 1
        await session.commit()
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
        assert await session.scalar(select(func.count()).select_from(Message)) == 1


async def test_cross_workspace_reads_and_writes_are_denied(
    schema_database: Database, account
) -> None:
    outsider = await make_account(schema_database, "outsider@example.com")
    async with schema_database.session() as session:
        other_repository = Repository(session, outsider.user.id)
        other_conversation = await other_repository.create_conversation(
            outsider.workspace.id, title="다른 사용자의 대화", model="test-model"
        )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        attempts = [
            lambda: repository.get_workspace(outsider.workspace.id),
            lambda: repository.list_conversations(outsider.workspace.id),
            lambda: repository.create_conversation(outsider.workspace.id, model="test-model"),
            lambda: repository.get_conversation(outsider.workspace.id, other_conversation.id),
            lambda: repository.get_conversation(account.workspace.id, other_conversation.id),
            lambda: repository.append_message(
                account.workspace.id, other_conversation.id, role="user", content="침범"
            ),
            lambda: repository.list_messages(account.workspace.id, other_conversation.id),
            lambda: ConversationService(session, repository.actor_id).soft_delete_conversation(
                account.workspace.id, other_conversation.id
            ),
        ]
        for attempt in attempts:
            with pytest.raises(AccessDenied):
                await attempt()
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert (await session.get(Conversation, other_conversation.id)).deleted_at is None


async def test_member_can_participate_but_only_admin_can_delete_another_users_conversation(
    schema_database: Database, account
) -> None:
    participant = await make_account(schema_database, "participant@example.com")
    async with schema_database.session() as session:
        session.add(
            WorkspaceMember(
                workspace_id=account.workspace.id, user_id=participant.user.id, role="member"
            )
        )
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model="test-model"
        )
        await session.commit()
    async with schema_database.session() as session:
        repository = Repository(session, participant.user.id)
        assert (
            await repository.get_conversation(account.workspace.id, conversation.id)
        ).id == conversation.id
        message = await repository.append_message(
            account.workspace.id, conversation.id, role="user", content="참여자 메시지"
        )
        assert message.created_by == participant.user.id
        with pytest.raises(AccessDenied):
            await ConversationService(session, repository.actor_id).soft_delete_conversation(
                account.workspace.id, conversation.id
            )
        await session.commit()
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == account.workspace.id,
                WorkspaceMember.user_id == participant.user.id,
            )
            .values(role="admin")
        )
    async with schema_database.session() as session:
        await ConversationService(session, participant.user.id).soft_delete_conversation(
            account.workspace.id, conversation.id
        )
        await session.commit()
    async with schema_database.session() as session:
        assert (await session.get(Conversation, conversation.id)).deleted_at is not None


@pytest.mark.parametrize("blocked_state", ["disabled", "archived", "revoked", "unknown_actor"])
async def test_inactive_or_missing_membership_denies_access(
    schema_database: Database, account, blocked_state: str
) -> None:
    async with schema_database.session() as session:
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model="test-model"
        )
        await session.commit()
    actor_id = account.user.id
    async with schema_database.engine.begin() as connection:
        if blocked_state == "disabled":
            await connection.execute(
                update(User).where(User.id == actor_id).values(status="disabled")
            )
        elif blocked_state == "archived":
            await connection.execute(
                update(Workspace)
                .where(Workspace.id == account.workspace.id)
                .values(status="archived")
            )
        elif blocked_state == "revoked":
            await connection.execute(
                text("DELETE FROM workspace_members WHERE user_id = :actor"), {"actor": actor_id}
            )
        else:
            actor_id = uuid4()

    async with schema_database.session() as session:
        repository = Repository(session, actor_id)
        for attempt in [
            lambda: repository.get_workspace(account.workspace.id),
            lambda: repository.list_conversations(account.workspace.id),
            lambda: repository.create_conversation(account.workspace.id, model="test-model"),
            lambda: repository.get_conversation(account.workspace.id, conversation.id),
            lambda: repository.append_message(
                account.workspace.id, conversation.id, role="user", content="접근 금지"
            ),
            lambda: repository.list_messages(account.workspace.id, conversation.id),
            lambda: ConversationService(session, repository.actor_id).soft_delete_conversation(
                account.workspace.id, conversation.id
            ),
        ]:
            with pytest.raises(AccessDenied):
                await attempt()


async def test_conversation_cursor_has_no_gaps_with_tied_dates_and_empty_conversations(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        conversations = [
            await repository.create_conversation(
                account.workspace.id, title=f"빈 대화 {index}", model="test-model"
            )
            for index in range(105)
        ]
        same_time = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
        await session.execute(update(Conversation).values(last_message_at=same_time))
        await session.execute(
            update(Conversation)
            .where(Conversation.id.in_([row.id for row in conversations[:35]]))
            .values(last_message_at=same_time - timedelta(days=1))
        )
        await session.commit()

    async with schema_database.session() as session:
        expected = list(
            (
                await session.scalars(
                    select(Conversation.id).order_by(
                        Conversation.last_message_at.desc(), Conversation.id.desc()
                    )
                )
            ).all()
        )
        repository = Repository(session, account.user.id)
        seen = []
        cursor = None
        for _ in range(8):
            page = await repository.list_conversations(
                account.workspace.id, limit=17, cursor=cursor
            )
            seen.extend(row.id for row in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
            assert isinstance(cursor, str)
        assert cursor is None
        assert seen == expected
        assert len(seen) == len(set(seen)) == 105
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        with pytest.raises(InvalidInput):
            await repository.list_conversations(account.workspace.id, cursor="not-a-valid-cursor")


async def test_conversation_search_matches_title_and_body_with_status_and_access_scope(
    schema_database: Database, account
) -> None:
    outsider = await make_account(schema_database, "search-outsider@example.com")
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        title_match = await repository.create_conversation(
            account.workspace.id, title="한글 Recipe 제목", model="test-model"
        )
        body_match = await repository.create_conversation(
            account.workspace.id, title="다른 제목", model="test-model"
        )
        deleted = await repository.create_conversation(
            account.workspace.id, title="삭제한 Recipe", model="test-model"
        )
        for conversation in (body_match, deleted):
            await repository.append_message(
                account.workspace.id, conversation.id, role="assistant", content="recipe 본문"
            )
        await repository.update_conversation(account.workspace.id, body_match.id, status="archived")
        await ConversationService(session, repository.actor_id).soft_delete_conversation(
            account.workspace.id, deleted.id
        )
        await Repository(session, outsider.user.id).create_conversation(
            outsider.workspace.id, title="외부 Recipe", model="test-model"
        )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        active = await repository.list_conversations(account.workspace.id, q=" recipe ")
        assert [row.id for row in active.items] == [title_match.id]
        archived = await repository.list_conversations(
            account.workspace.id, q="recipe", status="archived"
        )
        assert [row.id for row in archived.items] == [body_match.id]
        all_statuses = await repository.list_conversations(
            account.workspace.id, q="RECIPE", status="all"
        )
        assert {row.id for row in all_statuses.items} == {title_match.id, body_match.id}
        korean = await repository.list_conversations(account.workspace.id, q="한글")
        assert [row.id for row in korean.items] == [title_match.id]
        for q in (None, "", " \n\t "):
            recent = await repository.list_conversations(account.workspace.id, q=q)
            assert [row.id for row in recent.items] == [title_match.id]
        for q in ("x" * 201, "\x00", 123):
            with pytest.raises(InvalidInput):
                await repository.list_conversations(account.workspace.id, q=q)
        with pytest.raises(AccessDenied):
            await repository.list_conversations(outsider.workspace.id, q="recipe", status="all")


async def test_conversation_search_treats_wildcards_and_sql_syntax_as_literal_text(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        matches = {}
        for literal in ("%", "_", "/"):
            title = await repository.create_conversation(
                account.workspace.id, title=f"제목 {literal} 리터럴", model="test-model"
            )
            body = await repository.create_conversation(
                account.workspace.id, title="본문에서 검색", model="test-model"
            )
            await repository.append_message(
                account.workspace.id, body.id, role="user", content=f"본문 {literal} 리터럴"
            )
            matches[literal] = {title.id, body.id}
        await repository.create_conversation(
            account.workspace.id, title="리터럴 없는 일반 대화", model="test-model"
        )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        for literal, expected in matches.items():
            page = await repository.list_conversations(account.workspace.id, q=literal)
            assert {row.id for row in page.items} == expected
        injection = await repository.list_conversations(account.workspace.id, q="' OR TRUE --")
        assert injection.items == []


async def test_conversation_search_pages_without_duplicates_from_multiple_matching_messages(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        expected = []
        for index in range(7):
            match = await repository.create_conversation(
                account.workspace.id, title=f"검색 대상 {index}", model="test-model"
            )
            for role in ("user", "assistant"):
                await repository.append_message(
                    account.workspace.id, match.id, role=role, content="반복 검색 대상"
                )
            if index % 2:
                await repository.update_conversation(
                    account.workspace.id, match.id, status="archived"
                )
            expected.append(match.id)
            await repository.create_conversation(
                account.workspace.id, title="무관한 대화", model="test-model"
            )
        await session.execute(
            update(Conversation).values(last_message_at=datetime(2026, 9, 9, tzinfo=UTC))
        )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        seen = []
        cursor = None
        for _ in range(4):
            page = await repository.list_conversations(
                account.workspace.id, q="검색 대상", status="all", limit=2, cursor=cursor
            )
            seen.extend(row.id for row in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert cursor is None
        assert seen == sorted(expected, reverse=True)
        assert len(seen) == len(set(seen)) == 7


async def test_message_before_cursor_returns_chronological_pages(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        conversation = await repository.create_conversation(
            account.workspace.id, model="test-model"
        )
        for number in range(1, 12):
            await repository.append_message(
                account.workspace.id, conversation.id, role="assistant", content=f"응답 {number}"
            )
        await session.commit()

    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        before = None
        all_sequences = []
        for expected in [[8, 9, 10, 11], [4, 5, 6, 7], [1, 2, 3]]:
            page = await repository.list_messages(
                account.workspace.id, conversation.id, limit=4, before=before
            )
            assert [row.sequence for row in page.items] == expected
            all_sequences.extend(row.sequence for row in page.items)
            before = page.next_cursor
        assert before is None
        assert sorted(all_sequences) == list(range(1, 12))
        empty = await repository.list_messages(account.workspace.id, conversation.id, before=1)
        assert empty.items == []
        assert empty.next_cursor is None
        with pytest.raises(InvalidInput):
            await repository.list_messages(account.workspace.id, conversation.id, before=0)


async def test_soft_delete_hides_conversation_but_preserves_messages(
    schema_database: Database, account
) -> None:
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        conversation = await repository.create_conversation(
            account.workspace.id, model="test-model"
        )
        await repository.append_message(
            account.workspace.id, conversation.id, role="user", content="보존할 원문"
        )
        await session.commit()
    async with schema_database.session() as session:
        await ConversationService(session, account.user.id).soft_delete_conversation(
            account.workspace.id, conversation.id
        )
        await session.commit()
    async with schema_database.session() as session:
        repository = Repository(session, account.user.id)
        assert (await repository.list_conversations(account.workspace.id)).items == []
        for attempt in [
            lambda: repository.get_conversation(account.workspace.id, conversation.id),
            lambda: repository.list_messages(account.workspace.id, conversation.id),
            lambda: repository.append_message(
                account.workspace.id, conversation.id, role="user", content="삭제 후 쓰기"
            ),
        ]:
            with pytest.raises(AccessDenied):
                await attempt()
        assert (await session.get(Conversation, conversation.id)).deleted_at is not None
        assert (await session.scalars(select(Message))).one().content == "보존할 원문"


async def test_concurrent_append_serializes_sequence_and_rollback_reuses_it(
    schema_database: Database, postgres: IsolatedPostgres, account
) -> None:
    async with schema_database.session() as session:
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model="test-model"
        )
        await session.commit()
    other_database = Database(database_settings(postgres))
    first_written = asyncio.Event()
    release_first = asyncio.Event()

    async def append(database: Database, hold: bool) -> int:
        async with database.session() as session:
            row = await Repository(session, account.user.id).append_message(
                account.workspace.id, conversation.id, role="user", content="동시 저장"
            )
            if hold:
                first_written.set()
                await release_first.wait()
            await session.commit()
            return row.sequence

    first_task = asyncio.create_task(append(schema_database, True))
    second_task = None
    blocked = False
    try:
        await asyncio.wait_for(first_written.wait(), timeout=5)
        second_task = asyncio.create_task(append(other_database, False))
        try:
            async with asyncio.timeout(5):
                while not second_task.done():
                    blocked = bool(
                        await postgres.admin.fetchval(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = $1 AND wait_event_type = 'Lock')",
                            postgres.name,
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
        finally:
            release_first.set()
        sequences = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
        assert blocked, "The second transaction must wait until the first append is committed"
        assert sorted(sequences) == [1, 2]
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task), return_exceptions=True
        )
        await other_database.dispose()

    async with schema_database.session() as session:
        rolled_back = await Repository(session, account.user.id).append_message(
            account.workspace.id, conversation.id, role="user", content="되돌릴 메시지"
        )
        assert rolled_back.sequence == 3
    async with schema_database.session() as session:
        saved = await Repository(session, account.user.id).append_message(
            account.workspace.id, conversation.id, role="user", content="다시 저장"
        )
        assert saved.sequence == 3
        await session.commit()
    async with schema_database.session() as session:
        assert list(
            (await session.scalars(select(Message.sequence).order_by(Message.sequence))).all()
        ) == [1, 2, 3]
        assert (await session.get(Conversation, conversation.id)).next_message_sequence == 4
