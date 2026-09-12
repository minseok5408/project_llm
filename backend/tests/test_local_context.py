"""기억과 원문 회수의 격리·변경 전파·문맥 한도·후정산을 검증한다."""

import asyncio
import json
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.context.builder import list_context_turns
from backend.app.context.local import LOCAL_CONTEXT_PROMPT
from backend.app.context.recall import excerpt, recall_originals, recall_terms
from backend.app.context.service import latest_summary
from backend.app.models import (
    Conversation,
    ConversationCompaction,
    GenerationRun,
    GenerationStep,
    Message,
    User,
    WorkspaceMember,
)
from backend.app.repositories import Repository
from backend.app.services.memories import MemoryService
from backend.tests.test_generations import balance, snapshot
from backend.tests.test_generations import harness as harness
from backend.tests.test_web_search_generation import install


def test_recall_terms_ignore_instructions_and_preserve_identifiers():
    assert recall_terms("아까 정했던 api_timeout 값을 다시 알려줘") == ["api_timeout"]
    assert recall_terms("Python과 timeout을 알려줘") == ["python", "timeout"]
    assert len(recall_terms(" ".join(f"key{i}" for i in range(100)))) == 8
    assert recall_terms("그때 뭐였지?") == []
    value, start, end = excerpt(
        "앞부분 ß 😊 " + "앞" * 150 + "timeout=37" + "뒤" * 500, ["timeout"], 100
    )
    assert "timeout=37" in value and end - start == len(value) == 100


async def change(harness, account=None, **kwargs):
    account = account or harness.system
    async with harness.database.session() as session:
        revision = (await session.get(User, account.user_id)).memory_revision
        result = await MemoryService(session, account.user_id).change(revision=revision, **kwargs)
        await session.commit()
        return result


async def summary(harness, run_id, *, dependencies=None):
    saved = await snapshot(harness.database, run_id)
    async with harness.database.session() as session:
        session.add(
            ConversationCompaction(
                workspace_id=saved.run.workspace_id,
                conversation_id=saved.run.conversation_id,
                generation_id=saved.run.id,
                model=harness.settings.llm_model_id,
                through_sequence=saved.assistant.sequence,
                status="completed",
                content="이전 작업을 논의했다.",
                input_tokens=100,
                output_tokens=10,
                usage_basis="provider",
                memory_dependencies=dependencies or {},
            )
        )
        await session.commit()


def local_record(messages):
    if not any(message.content == LOCAL_CONTEXT_PROMPT for message in messages):
        return None
    return json.loads(messages[-2].content)


@pytest.mark.postgres
@pytest.mark.parametrize("account_name", ["system", "member"])
async def test_memory_reused_in_new_chat_is_local_and_charged_once(harness, account_name):
    account = getattr(harness, account_name)
    if account_name == "member":
        await harness.grant()
    await change(harness, account, key="직업", content="private-memory-sentinel 개발자")
    search, model = install(harness)
    before = await balance(harness.database, account)
    async with harness.database.session() as session:
        conversation = await Repository(session, account.user_id).create_conversation(
            account.workspace_id, model=harness.settings.llm_model_id
        )
        fresh = replace(account, conversation_id=conversation.id)
        await session.commit()
    request = await harness.submit(fresh, content="최신 Python 정보를 검색해줘")
    pending = await snapshot(harness.database, request["id"])
    assert "private-memory-sentinel" not in str(pending.run.request_messages)
    assert (await balance(harness.database, account)).used_tokens == before.used_tokens
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    messages, options = model.calls[-1]
    assert local_record(messages)["saved_memories"][0]["content"].startswith(
        "private-memory-sentinel"
    )
    assert "private-memory-sentinel" not in str(search.search_calls)
    assert "private-memory-sentinel" not in str([event.payload for event in saved.events])
    assert saved.run.status == "completed" and saved.run.request_messages == []
    assert saved.run.memory_dependencies == {str(account.user_id): 1}
    assert saved.reservation.input_tokens == await model.count_input(messages, options)
    await harness.service.finish(saved.run.id)
    after = await balance(harness.database, account)
    assert (
        after.used_tokens - before.used_tokens
        == saved.reservation.input_tokens + saved.reservation.output_tokens
    )
    async with harness.database.session() as session:
        steps = (
            await session.scalars(
                select(GenerationStep).where(GenerationStep.generation_id == saved.run.id)
            )
        ).all()
        assert next(step for step in steps if step.name == "local_context").input_tokens == 0


@pytest.mark.postgres
async def test_other_account_and_ordinary_chat_never_create_personal_memories(harness):
    await change(harness, key="이름", content="private-sentinel")
    _, model = install(harness)
    await harness.grant(harness.other)
    request = await harness.submit(harness.other, content="나는 개발자야. 기억해줘")
    await harness.execute_next()
    assert "private-sentinel" not in str(model.calls)
    assert local_record(model.calls[-1][0]) is None
    async with harness.database.session() as session:
        assert (await MemoryService(session, harness.other.user_id).snapshot())["items"] == []
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"


@pytest.mark.postgres
async def test_memory_change_invalidates_answers_descendants_and_summaries(harness):
    data = await change(harness, key="직업", content="old-memory-sentinel")
    item = data["items"][0]
    _, model = install(harness)
    first = await harness.submit(content="내 직업을 알려줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, first["id"])
    async with harness.database.session() as session:
        answer = await session.get(Message, saved.assistant.id)
        answer.content = "old-memory-sentinel"
        session.add(
            WorkspaceMember(
                workspace_id=harness.system.workspace_id,
                user_id=harness.other.user_id,
                role="member",
            )
        )
        await session.commit()
    await harness.grant(harness.other)
    shared = replace(
        harness.other,
        workspace_id=harness.system.workspace_id,
        conversation_id=harness.system.conversation_id,
    )
    second = await harness.submit(shared, content="아까 답변을 이어서 설명해줘")
    await harness.execute_next()
    descendant = await snapshot(harness.database, second["id"])
    assert descendant.run.memory_dependencies == saved.run.memory_dependencies
    await summary(harness, second["id"], dependencies=descendant.run.memory_dependencies)
    await change(
        harness, memory_id=UUID(item["id"]), key="직업", content="corrected-memory-sentinel"
    )
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, harness.system.conversation_id)
        assert await latest_summary(session, conversation) == (None, 0)
        turns = await list_context_turns(session, conversation)
        assert len(turns) == 2 and all(turn.assistant_content is None for turn in turns)
        assert (await session.get(Message, saved.assistant.id)).content == "old-memory-sentinel"
    request = await harness.submit(content="현재 직업이 뭐야?")
    await harness.execute_next()
    assert "old-memory-sentinel" not in str(model.calls[-1])
    assert "corrected-memory-sentinel" in str(model.calls[-1])
    await change(harness, memory_id=UUID(item["id"]), delete=True)
    await harness.submit(content="내 직업을 알아?")
    await harness.execute_next()
    assert "memory-sentinel" not in str(model.calls[-1])
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"


@pytest.mark.postgres
@pytest.mark.parametrize("phase", ["queued", "running"])
async def test_memory_change_stops_active_generation_without_guessing_usage(harness, phase):
    data = await change(harness, key="이름", content="옛 이름")
    _, model = install(harness)
    model.blocked = phase == "running"
    request = await harness.submit(content="이름을 알려줘")
    task = None
    if phase == "running":
        task = asyncio.create_task(harness.execute_next())
        await asyncio.wait_for(model.started.wait(), 5)
    try:
        await change(harness, memory_id=UUID(data["items"][0]["id"]), delete=True)
        if task:
            await asyncio.wait_for(task, 5)
        else:
            await harness.execute_next()
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "cancelled"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    assert saved.assistant.content == ""


@pytest.mark.postgres
async def test_original_recall_keeps_offsets_source_scope_and_token_bounds(harness):
    _, model = install(harness)
    first = await harness.submit(content="배포 설정: api_timeout=37, retry_limit=4로 결정했어.")
    await harness.execute_next()
    await summary(harness, first["id"])
    request = await harness.submit(content="아까 api_timeout 값을 다시 알려줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    messages, options = model.calls[-1]
    record = local_record(messages)
    assert saved.run.status == "completed" and record["originals"]
    assert "api_timeout=37" in str(record["originals"])
    assert saved.run.recall_sources == [
        {key: value for key, value in source.items() if key != "content"}
        for source in record["originals"]
    ]
    async with harness.database.session() as session:
        for source in record["originals"]:
            original = await session.get(Message, UUID(source["message_id"]))
            assert original.content[source["start"] : source["end"]] == source["content"]
            assert original.sequence <= 2
        other = await session.get(Conversation, harness.other.conversation_id)
        assert await recall_originals(session, other, "api_timeout", 999) == []
    assert saved.reservation.input_tokens == await model.count_input(messages, options)
    assert saved.run.prompt_tokens + options.max_tokens <= harness.settings.llm_context_window


@pytest.mark.postgres
async def test_recall_skips_unrelated_terms_and_omits_when_budget_is_too_small(harness):
    _, model = install(harness)
    first = await harness.submit(content="api_timeout=37로 정했어")
    await harness.execute_next()
    await summary(harness, first["id"])
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, harness.system.conversation_id)
        assert await recall_originals(session, conversation, "banana", 2) == []
    normal_count = model.count_input

    async def expensive(messages, options):
        if any(message.content == LOCAL_CONTEXT_PROMPT for message in messages):
            return harness.settings.llm_context_window
        return await normal_count(messages, options)

    model.count_input = expensive
    request = await harness.submit(content="api_timeout 알려줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed" and saved.run.recall_sources == []
    assert local_record(model.calls[-1][0]) is None
    assert not saved.run.memory_dependencies


def test_conflicting_memory_generations_are_never_merged():
    from backend.app.context.dependencies import merge_dependencies
    from backend.app.repositories import Conflict

    with pytest.raises(Conflict):
        merge_dependencies({"owner": 1}, {"owner": 2})
    assert merge_dependencies({"owner": 2}, {"owner": 2, "other": 3}) == {"owner": 2, "other": 3}


@pytest.mark.postgres
async def test_memory_is_excluded_from_native_search_planning(harness):
    from backend.tests.test_search_agent import install as install_agent

    await change(harness, key="직업", content="private-planning-sentinel")
    search, model = install_agent(harness)
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert model.plan_calls and search.search_calls
    assert "private-planning-sentinel" not in str(model.plan_calls)
    assert "private-planning-sentinel" not in str(search.search_calls)
    assert "private-planning-sentinel" in str(model.calls[-1])
    assert saved.run.status == "completed"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 100 * len(model.plan_calls)


@pytest.mark.postgres
async def test_permission_is_rechecked_after_local_token_count(harness):
    from sqlalchemy import delete

    await change(harness, harness.member, key="직업", content="개발자")
    await harness.grant()
    _, model = install(harness)
    started, release = asyncio.Event(), asyncio.Event()
    original_count = model.count_input

    async def waiting(messages, options):
        if any(message.content == LOCAL_CONTEXT_PROMPT for message in messages):
            started.set()
            await release.wait()
        return await original_count(messages, options)

    model.count_input = waiting
    request = await harness.submit(harness.member, content="내 직업을 알려줘")
    running = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with harness.database.session() as session:
            await session.execute(
                delete(WorkspaceMember).where(WorkspaceMember.user_id == harness.member.user_id)
            )
            await session.commit()
        release.set()
        await asyncio.wait_for(running, 5)
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    saved = await snapshot(harness.database, request["id"])
    assert not model.calls and saved.run.error_code == "access_revoked"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0


@pytest.mark.postgres
async def test_memory_change_during_answer_charges_only_received_tokens(harness):
    from backend.app.llm.protocol import ProviderDelta

    data = await change(harness, harness.member, key="직업", content="개발자")
    await harness.grant()
    _, model = install(harness)
    started = asyncio.Event()

    async def partial(messages, options):
        yield ProviderDelta(text="확인된 답변", received_output_tokens=2)
        started.set()
        await asyncio.Event().wait()

    model.stream = partial
    request = await harness.submit(harness.member, content="내 직업을 알려줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(started.wait(), 5)
        await change(harness, harness.member, memory_id=UUID(data["items"][0]["id"]), delete=True)
        await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "cancelled"
    assert saved.reservation.usage_basis == "received"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens
    assert saved.reservation.output_tokens == 2
    before = (await balance(harness.database, harness.member)).used_tokens
    await harness.service.finish(saved.run.id)
    assert (await balance(harness.database, harness.member)).used_tokens == before


@pytest.mark.postgres
async def test_memory_change_during_admission_rejects_stale_context(harness):
    from backend.app.repositories import Conflict

    await change(harness, key="직업", content="개발자")
    _, model = install(harness)
    started, release = asyncio.Event(), asyncio.Event()
    original_count = model.count_input

    async def waiting(messages, options):
        started.set()
        await release.wait()
        return await original_count(messages, options)

    model.count_input = waiting
    task = asyncio.create_task(harness.submit(content="내 직업이 뭐야?"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await change(harness, key="선호", content="간결한 답변")
        release.set()
        with pytest.raises(Conflict):
            await asyncio.wait_for(task, 5)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with harness.database.session() as session:
        assert not (await session.scalars(select(GenerationRun))).all()
