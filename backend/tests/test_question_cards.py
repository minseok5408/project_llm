"""질문 카드의 영속 응답·중복 승인·중단과 실제 사용량을 검증한다."""

import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable, ToolCall
from backend.app.models import GenerationRun, User
from backend.app.repositories import AccessDenied, Conflict
from backend.app.schemas import GenerationOptions
from backend.app.services.token_quota import QuotaExceeded
from backend.app.tools.web_search.planning import query_entries
from backend.tests.conftest import run_alembic, version_rows
from backend.tests.test_generations import balance, snapshot
from backend.tests.test_generations import harness as harness
from backend.tests.test_web_search_generation import RecordingChatProvider, finish_task

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
CARD = {"questions": [{"question": "어떤 결과물이 필요한가요?", "options": ["문서", "코드"]}]}


class QuestionProvider(RecordingChatProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.card = CARD
        self.question_started = asyncio.Event()
        self.question_release = asyncio.Event()
        self.question_blocked = False
        self.tokenizer_unavailable = False
        self.bad_usage = False
        self.tool_inputs = []

    async def count_tools(self, messages, options, tools):
        if self.tokenizer_unavailable:
            raise ProviderUnavailable("질문 tokenizer 미지원", request_started=False)
        return await self.count_input(messages, options) + 10

    async def stream_tools(self, messages, options, tools):
        self.tool_inputs.append((messages, tools, options))
        self.question_started.set()
        if self.card is None:
            async for delta in self.stream(messages, options):
                yield replace(delta, input_tokens=delta.input_tokens + 10) if delta.final else delta
            return
        yield ProviderDelta(received_output_tokens=3)
        if self.question_blocked:
            await self.question_release.wait()
        yield ProviderDelta(
            final=True,
            input_tokens=0 if self.bad_usage else await self.count_tools(messages, options, tools),
            output_tokens=32,
            received_output_tokens=32,
            finish_reason="tool_calls",
            tool_calls=(ToolCall("question_1", "ask_user_question", json.dumps(self.card)),),
        )


def install(harness):
    model = QuestionProvider(harness.settings)
    harness.provider = harness.service.provider = model
    return model


async def make_card(harness, account=None, *, thinking=False):
    request = await harness.submit(
        account,
        content="프로젝트 결과물을 만들어줘",
        options=GenerationOptions(thinking=thinking, max_tokens=512),
    )
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed", saved.run.error_code
    assert saved.run.question_card == CARD
    return saved


async def respond(harness, saved, *, key=None, answers=None, account=None):
    return await harness.service.respond(
        (account or harness.system).user_id,
        saved.run.id,
        answers=answers or ["Python 코드"],
        options=GenerationOptions(max_tokens=512),
        idempotency_key=key or uuid4(),
        network_mode="local",
        web_search="off",
    )


async def test_question_releases_queue_and_response_uses_saved_questions_with_current_policy(
    harness,
):
    model = install(harness)
    await harness.grant(limit=20000)
    saved = await make_card(harness, harness.member, thinking=True)
    assert saved.run.thinking is model.tool_inputs[-1][2].thinking is True
    assert saved.run.max_output_tokens == model.tool_inputs[-1][2].max_tokens
    assert saved.assistant.content.endswith(CARD["questions"][0]["question"])
    assert saved.reservation.input_tokens == saved.run.prompt_tokens
    assert saved.reservation.output_tokens == 32
    assert all(item["status"] == "completed" for item in saved.run.progress)
    assert await harness.worker.claim() is None
    before = await balance(harness.database, harness.member)
    model.card = None
    accepted = await respond(harness, saved, account=harness.member)
    during = await balance(harness.database, harness.member)
    assert during.used_tokens == before.used_tokens and during.reserved_tokens == 0
    original = await snapshot(harness.database, saved.run.id)
    assert original.run.question_card["answers"] == ["Python 코드"]
    assert original.run.question_card["response_generation_id"] == accepted["id"]
    assert original.assistant.content == saved.assistant.content
    await harness.execute_next()
    final = await snapshot(harness.database, accepted["id"])
    assert final.run.status == "completed" and final.run.question_card is None
    assert final.run.thinking is model.tool_inputs[-1][2].thinking is False
    assert final.run.max_output_tokens == model.tool_inputs[-1][2].max_tokens
    assert final.run.network_mode == "local" and final.run.web_search_mode == "off"
    assert "어떤 결과물" not in final.user_message.content
    assert "Python 코드" in final.user_message.content
    assert model.tool_inputs[-1][0][-1].content == final.user_message.content
    assert any(
        item.role == "assistant" and "어떤 결과물" in item.content
        for item in model.tool_inputs[-1][0]
    )
    entries = query_entries(
        {"messages": [item.model_dump() for item in model.tool_inputs[-1][0]]},
        final.user_message.content,
    )
    assert all("어떤 결과물" not in entry for entry in entries.values())
    assert all(item["status"] == "completed" for item in final.run.progress)


async def test_question_response_retries_and_racing_keys_create_one_generation(harness):
    install(harness)
    saved = await make_card(harness)
    key = uuid4()
    results = await asyncio.gather(
        respond(harness, saved, key=key), respond(harness, saved, key=key), return_exceptions=True
    )
    accepted = next(item for item in results if isinstance(item, dict))
    assert (await respond(harness, saved, key=key))["id"] == accepted["id"]
    with pytest.raises(Conflict):
        await respond(harness, saved)
    with pytest.raises(Conflict):
        await respond(harness, saved, key=key, answers=["다른 답"])
    async with harness.database.session() as session:
        rows = (
            await session.scalars(
                select(GenerationRun).where(
                    GenerationRun.conversation_id == saved.run.conversation_id
                )
            )
        ).all()
        assert len(rows) == 2


@pytest.mark.parametrize("change", ["new_message", "regenerate", "memory", "other_user", "archive"])
async def test_stale_and_unowned_cards_cannot_start_work(harness, change):
    install(harness)
    saved = await make_card(harness)
    if change == "new_message":
        await harness.submit()
    elif change == "regenerate":
        await harness.service.regenerate(
            harness.system.user_id,
            saved.run.id,
            options=GenerationOptions(max_tokens=512),
            idempotency_key=uuid4(),
        )
    elif change == "memory":
        async with harness.database.session() as session:
            run = await session.get(GenerationRun, saved.run.id)
            run.memory_dependencies = {str(harness.system.user_id): 0}
            (await session.get(User, harness.system.user_id)).memory_revision = 1
            await session.commit()
    elif change == "archive":
        from backend.app.models import Conversation

        async with harness.database.session() as session:
            (await session.get(Conversation, saved.run.conversation_id)).status = "archived"
            await session.commit()
    with pytest.raises((Conflict, AccessDenied)):
        await respond(harness, saved, account=harness.other if change == "other_user" else None)
    assert not (await snapshot(harness.database, saved.run.id)).run.question_card.get(
        "response_generation_id"
    )


async def test_question_quota_rejection_keeps_card_unanswered(harness):
    install(harness)
    saved = await make_card(harness)
    await harness.grant(harness.system, limit=0)
    async with harness.database.session() as session:
        (await session.get(User, harness.system.user_id)).platform_role = "member"
        await session.commit()
    with pytest.raises(QuotaExceeded):
        await respond(harness, saved)
    assert (await snapshot(harness.database, saved.run.id)).run.question_card == CARD


async def test_cancel_question_stream_records_received_usage_and_no_card(harness):
    model = install(harness)
    model.question_blocked = True
    request = await harness.submit(options=GenerationOptions(max_tokens=512))
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(model.question_started.wait(), 5)
        await asyncio.sleep(0.03)
        await harness.service.cancel(harness.system.user_id, UUID(request["id"]))
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "cancelled" and saved.run.question_card is None
    assert saved.reservation.output_tokens == 3 and saved.reservation.usage_basis == "received"
    assert saved.run.progress[-1]["status"] == "cancelled"
    assert not any(item["status"] in ("pending", "running") for item in saved.run.progress)


@pytest.mark.parametrize("invalid", ["arguments", "usage"])
async def test_invalid_question_never_becomes_actionable_and_only_valid_usage_is_charged(
    harness, invalid
):
    model = install(harness)
    model.card = {"questions": []} if invalid == "arguments" else CARD
    model.bad_usage = invalid == "usage"
    request = await harness.submit(options=GenerationOptions(max_tokens=512))
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "failed" and saved.run.question_card is None
    assert saved.reservation.output_tokens == (32 if invalid == "arguments" else 0)
    assert saved.run.progress[-1]["status"] == "failed"


@pytest.mark.parametrize("fallback", ["tokenizer", "budget", "disabled"])
async def test_question_unavailable_falls_back_to_plain_answer(harness, fallback):
    model = install(harness)
    model.tokenizer_unavailable = fallback == "tokenizer"
    harness.settings.generation_questions_enabled = fallback != "disabled"
    request = await harness.submit(
        options=GenerationOptions(max_tokens=16 if fallback == "budget" else 512)
    )
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed" and saved.run.question_card is None
    assert not model.tool_inputs


async def test_question_history_blocks_lossy_downgrade(harness, postgres):
    install(harness)
    saved = await make_card(harness)
    await harness.database.dispose()
    await run_alembic(postgres, "downgrade", "0014_memory_recall", success=False)
    assert await version_rows(harness.database) == ["0017_schema_roles"]
    assert (await snapshot(harness.database, saved.run.id)).run.question_card == CARD
