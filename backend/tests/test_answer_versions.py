"""재생성의 원문 보존, 문맥 선택, 멱등성과 종료 후 과금을 검증한다."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from backend.app.context.builder import list_context_turns
from backend.app.models import Conversation, Message
from backend.app.repositories import AccessDenied, Conflict
from backend.app.schemas import GenerationOptions
from backend.app.services.generations.admission import QueueFull
from backend.tests.conftest import run_alembic
from backend.tests.test_database import version_rows
from backend.tests.test_generation_api import (
    execute_next,
    login_new_account,
    new_conversation,
    submit,
)
from backend.tests.test_generation_api import (
    generation_client as generation_client,
)
from backend.tests.test_generations import (
    Harness,
    balance,
    snapshot,
    table_counts,
)
from backend.tests.test_generations import (
    harness as harness,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def regenerate(harness: Harness, run: dict, **kwargs) -> dict:
    return await harness.service.regenerate(
        kwargs.pop("actor_id", harness.system.user_id),
        UUID(run["id"]),
        options=kwargs.pop("options", GenerationOptions(max_tokens=64)),
        idempotency_key=kwargs.pop("key", uuid4()),
        **kwargs,
    )


async def test_regeneration_preserves_question_answer_and_uses_only_current_context(harness):
    first = await harness.submit(content="내 직업을 설명해 줘")
    await harness.execute_next()
    original = await snapshot(harness.database, first["id"])
    second = await regenerate(harness, first)
    queued = await snapshot(harness.database, second["id"])
    assert second["user_message_id"] == first["user_message_id"]
    assert second["assistant_message_id"] != first["assistant_message_id"]
    assert second["supersedes_generation_id"] == first["id"]
    assert queued.run.request_messages[-1] == {"role": "user", "content": "내 직업을 설명해 줘"}
    assert len(queued.run.request_messages) == 2
    previous = await snapshot(harness.database, first["id"])
    assert previous.assistant.content == original.assistant.content
    assert previous.reservation.input_tokens == original.reservation.input_tokens
    assert previous.run.is_current is False
    await harness.execute_next()
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, harness.system.conversation_id)
        turns = await list_context_turns(session, conversation)
        assert len(turns) == 1
        assert turns[0].assistant_sequence == queued.assistant.sequence
        assert (
            await session.scalar(
                select(func.count()).select_from(Message).where(Message.role == "user")
            )
            == 1
        )
    third = await harness.submit(content="다음 질문")
    third_snapshot = await snapshot(harness.database, third["id"])
    assert (
        len([item for item in third_snapshot.run.request_messages if item["role"] == "user"]) == 2
    )
    await harness.service.cancel(harness.system.user_id, UUID(third["id"]))


async def test_regeneration_idempotency_conflicts_and_concurrent_admission(harness):
    first = await harness.submit()
    await harness.execute_next()
    key = uuid4()
    second = await regenerate(harness, first, key=key)
    counts = await table_counts(harness.database)
    assert (await regenerate(harness, first, key=key))["id"] == second["id"]
    assert await table_counts(harness.database) == counts
    with pytest.raises(Conflict):
        await regenerate(harness, first, key=key, options=GenerationOptions(max_tokens=32))
    with pytest.raises(QueueFull):
        await regenerate(harness, first)
    await harness.execute_next()
    with pytest.raises(Conflict):
        await regenerate(harness, first)
    assert (await regenerate(harness, first, key=key))["id"] == second["id"]


@pytest.mark.parametrize("status", ["cancelled", "failed", "completed"])
async def test_retry_terminal_answer_is_separately_postcharged(harness, status):
    await harness.grant()
    account = harness.member
    first = await harness.submit(account)
    if status == "cancelled":
        await harness.service.cancel(account.user_id, UUID(first["id"]))
    elif status == "failed":
        await harness.service.finish(
            UUID(first["id"]), error_code="test_failure", never_started=True
        )
    else:
        await harness.execute_next()
    before = await balance(harness.database, account)
    second = await regenerate(harness, first, actor_id=account.user_id)
    assert await balance(harness.database, account) == before
    await harness.execute_next()
    finished = await snapshot(harness.database, second["id"])
    assert finished.run.status == "completed"
    after = await balance(harness.database, account)
    assert (
        after.used_tokens - before.used_tokens
        == finished.reservation.input_tokens + finished.reservation.output_tokens
    )
    await harness.service.finish(
        UUID(second["id"]),
        input_tokens=finished.reservation.input_tokens,
        output_tokens=finished.reservation.output_tokens,
    )
    assert await balance(harness.database, account) == after


async def test_other_user_old_question_and_archived_conversation_cannot_regenerate(harness):
    first = await harness.submit()
    await harness.execute_next()
    with pytest.raises(AccessDenied):
        await regenerate(harness, first, actor_id=harness.other.user_id)
    second = await harness.submit(content="새 질문")
    await harness.execute_next()
    with pytest.raises(Conflict):
        await regenerate(harness, first)
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, harness.system.conversation_id)
        conversation.status = "archived"
        await session.commit()
    with pytest.raises(Conflict):
        await regenerate(harness, second)


async def test_downgrade_refuses_to_destroy_answer_versions(harness, postgres):
    first = await harness.submit()
    await harness.execute_next()
    second = await regenerate(harness, first)
    await harness.execute_next()
    before = await table_counts(harness.database)
    await harness.database.dispose()
    await run_alembic(postgres, "downgrade", "0010_worker_heartbeat", success=False)
    assert await version_rows(harness.database) == ["0017_schema_roles"]
    assert await table_counts(harness.database) == before
    assert (await snapshot(harness.database, second["id"])).run.is_current


async def test_regeneration_api_metadata_auth_validation_and_sse(generation_client):
    client, app, provider = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    first = (await submit(client, conversation)).json()
    await execute_next(app)
    path = f"/api/v1/conversations/{conversation['id']}/messages"
    original = (await client.get(path)).json()["items"]
    assert original[-1]["can_regenerate"] is True
    endpoint = f"/api/v1/generations/{first['id']}/regenerate"
    key = str(uuid4())
    assert (await client.post(endpoint, json={})).status_code == 422
    assert (
        await client.post(endpoint, headers={"Idempotency-Key": key}, json={"content": "바꾼 질문"})
    ).status_code == 422
    csrf = client.headers.pop("X-CSRF-Token")
    assert (
        await client.post(endpoint, headers={"Idempotency-Key": key}, json={})
    ).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    accepted = await client.post(
        endpoint, headers={"Idempotency-Key": key}, json={"options": {"max_tokens": 64}}
    )
    assert accepted.status_code == 202, accepted.text
    second = accepted.json()
    queued = (await client.get(path)).json()["items"]
    assert len(queued) == 3
    assert queued[1]["content"] == original[1]["content"]
    assert queued[1]["is_current"] is False
    assert queued[-1]["is_current"] is True
    assert not any(item.get("can_regenerate") for item in queued)
    await execute_next(app)
    restored = (await client.get(path)).json()["items"]
    assert restored[-1]["can_regenerate"] is True
    assert restored[-1]["generation_id"] == second["id"]
    assert restored[-1]["generation_status"] == "completed"
    assert restored[-1]["finish_reason"] in ("stop", "length", None)
    replay = await client.get(second["events_url"])
    assert replay.status_code == 200 and "event: done" in replay.text
