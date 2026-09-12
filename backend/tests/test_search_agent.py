"""문맥 검색 반복의 실제 DB 정산·중단·한도·복구를 검증한다."""

import asyncio
import json
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.context.builder import compose_context
from backend.app.llm.protocol import ProviderDelta, ToolCall
from backend.app.models import GenerationStep, User
from backend.app.schemas import GenerationOptions
from backend.app.services.network_mode import NetworkModeService
from backend.tests.test_generations import balance, snapshot
from backend.tests.test_generations import harness as harness
from backend.tests.test_web_search_generation import (
    FakeSearchProvider,
    RecordingChatProvider,
    finish_task,
    search_record,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def search_call(text="최신 Python", message=0):
    return ToolCall(
        "search_1", "web_search", json.dumps({"terms": [{"message": message, "text": text}]})
    )


class PlanningProvider(RecordingChatProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.plans = [
            search_call(),
            ToolCall("finish_1", "finish_search", '{"reason":"sufficient"}'),
        ]
        self.plan_calls = []
        self.plan_started = asyncio.Event()
        self.plan_release = asyncio.Event()
        self.plan_blocked = False

    async def count_tools(self, messages, options, tools):
        if tools[0]["function"]["name"] == "ask_user_question":
            return await self.count_input(messages, options)
        return 100

    async def stream_tools(self, messages, options, tools):
        if tools[0]["function"]["name"] == "ask_user_question":
            async for delta in self.stream(messages, options):
                yield delta
            return
        self.plan_calls.append(messages)
        self.plan_started.set()
        yield ProviderDelta(received_output_tokens=3)
        if self.plan_blocked:
            await self.plan_release.wait()
        action = self.plans[min(len(self.plan_calls) - 1, len(self.plans) - 1)]
        yield ProviderDelta(
            final=True,
            input_tokens=100,
            output_tokens=20,
            received_output_tokens=20,
            finish_reason="tool_calls",
            tool_calls=(action,),
        )


def install(harness):
    harness.settings.web_search_agent_enabled = True
    search = FakeSearchProvider()
    model = PlanningProvider(harness.settings)
    harness.provider = harness.service.provider = model
    harness.service.search_provider = search
    harness.service.network_mode = NetworkModeService(harness.database, harness.settings, search)
    return search, model


async def steps(harness, run_id):
    async with harness.database.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(GenerationStep)
                    .where(GenerationStep.generation_id == UUID(str(run_id)))
                    .order_by(GenerationStep.sequence)
                )
            ).all()
        )
        session.expunge_all()
        return rows


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("안녕", id="greeting"),
        pytest.param("파이썬 리스트를 설명해줘", id="basic-concept"),
        pytest.param("'고마워'를 영어로 번역해줘", id="translation"),
        pytest.param("2 + 2는 얼마야?", id="calculation"),
    ],
)
async def test_auto_without_web_need_skips_search_planning_and_steps(harness, content):
    search, model = install(harness)
    request = await harness.submit(content=content, network_mode="auto", web_search="auto")
    await harness.execute_next()

    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == saved.assistant.status == "completed"
    assert saved.run.network_mode == saved.run.web_search_mode == "auto"
    assert search.check_calls == 0 and search.search_calls == []
    assert model.plan_calls == []
    assert len(model.calls) == 1
    assert await search_record(harness, request["id"]) is None
    assert [row.name for row in await steps(harness, request["id"])] == ["answer"]


async def test_context_search_and_review_settle_once_only_after_answer(harness):
    search, model = install(harness)
    await harness.grant()
    await harness.submit(harness.member, content="삼성전자에 대해 알려줘")
    await harness.execute_next()
    before = await balance(harness.database, harness.member)
    model.calls.clear()
    model.blocked = True
    model.started.clear()
    model.plans[0] = search_call("삼성전자", 1)
    request = await harness.submit(harness.member, content="그 회사 최신 뉴스를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        during = await balance(harness.database, harness.member)
        assert during.used_tokens == before.used_tokens and during.reserved_tokens == 0
        model.release.set()
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    saved = await snapshot(harness.database, request["id"])
    rows = await steps(harness, request["id"])
    assert search.search_calls == ["삼성전자"]
    assert [row.name for row in rows] == ["search_plan", "web_search", "search_review", "answer"]
    assert all(row.status == "completed" for row in rows)
    assert saved.run.status == "completed"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 200
    assert saved.reservation.output_tokens == saved.assistant.token_count + 40
    assert saved.reservation.input_tokens == sum(row.input_tokens for row in rows)
    assert saved.reservation.output_tokens == sum(row.output_tokens for row in rows)
    await harness.service.finish(UUID(request["id"]))
    after = await balance(harness.database, harness.member)
    assert (
        after.used_tokens - before.used_tokens
        == saved.reservation.input_tokens + saved.reservation.output_tokens
    )


async def test_research_attempt_limit_and_duplicate_query_stop(harness):
    search, model = install(harness)
    model.plans = [search_call(), search_call("Python")]
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    assert search.search_calls == ["최신 Python", "Python"]
    assert len(model.plan_calls) == 2
    assert len((await search_record(harness, request["id"])).sources) == 1
    model.plans = [search_call()]
    model.plan_calls.clear()
    search.search_calls.clear()
    await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    assert search.search_calls == ["최신 Python"]
    assert len(model.plan_calls) == 2


async def test_bad_tool_arguments_never_leave_server_but_planning_usage_is_charged(harness):
    search, model = install(harness)
    model.plans = [search_call("원문에 없는 private-sentinel")]
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert not search.search_calls and saved.run.status == "completed"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 100
    assert saved.reservation.output_tokens == saved.assistant.token_count + 20
    assert (await search_record(harness, request["id"])).reason == "planning_failed"


@pytest.mark.parametrize("phase", ["plan", "search"])
async def test_cancel_during_preparation_charges_confirmed_calls_and_no_answer(harness, phase):
    search, model = install(harness)
    await harness.grant()
    model.plan_blocked = phase == "plan"
    search.blocked = phase == "search"
    request = await harness.submit(harness.member, content="최신 Python 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(
            (model.plan_started if phase == "plan" else search.started).wait(), 5
        )
        await asyncio.sleep(0.03)
        await harness.service.cancel(harness.member.user_id, UUID(request["id"]))
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "cancelled" and not model.calls
    assert saved.reservation.input_tokens == 100
    assert saved.reservation.output_tokens == (3 if phase == "plan" else 20)
    assert saved.reservation.usage_basis == ("received" if phase == "plan" else "provider")
    assert all(row.status != "running" for row in await steps(harness, request["id"]))


async def test_local_switch_during_plan_prevents_search_and_keeps_answer_local(harness):
    search, model = install(harness)
    model.plan_blocked = True
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(model.plan_started.wait(), 5)
        await harness.service.network_mode.set_local_only(harness.system.user_id, True)
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    assert not search.search_calls
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    assert (await search_record(harness, request["id"])).reason == "mode_changed"


async def test_low_budget_skips_planning_without_reservation_or_extra_charge(harness):
    search, model = install(harness)
    content = "최신 Python 문서를 검색해줘"
    options = GenerationOptions(max_tokens=64)
    # 시스템 안내 길이가 달라져도 기본 답변만 가능한 예산 조건을 유지한다.
    limit = await model.count_input(compose_context([], content), options) + options.max_tokens
    await harness.grant(limit=limit)
    request = await harness.submit(harness.member, content=content, options=options)
    pending = await balance(harness.database, harness.member)
    assert pending.used_tokens == pending.reserved_tokens == 0
    await harness.execute_next()
    assert not search.search_calls and not model.plan_calls
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens
    current = await balance(harness.database, harness.member)
    assert current.used_tokens <= limit and current.reserved_tokens == 0


async def test_recovery_retains_completed_preparation_usage_without_replaying(harness):
    search, model = install(harness)
    search.blocked = True
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(search.started.wait(), 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        await finish_task(task)
    await harness.worker.recover()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "failed"
    assert (saved.reservation.input_tokens, saved.reservation.output_tokens) == (100, 20)
    assert saved.assistant.token_count == 0 and not model.calls
    assert len(search.search_calls) == 1


async def test_revoked_access_during_search_stops_before_answer_and_settles_planning(harness):
    search, model = install(harness)
    search.blocked = True
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(search.started.wait(), 5)
        async with harness.database.session() as session:
            user = await session.get(User, harness.system.user_id)
            user.status = "disabled"
            await session.commit()
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.error_code == "access_revoked" and saved.run.status == "failed"
    assert saved.reservation.input_tokens == 100 and saved.reservation.output_tokens == 20
    assert not model.calls and search.closed.is_set()


async def test_total_step_limit_keeps_room_for_answer(harness):
    search, model = install(harness)
    harness.settings.generation_max_steps = 3
    model.plans = [search_call(), search_call("Python")]
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    assert search.search_calls == ["최신 Python"]
    rows = await steps(harness, request["id"])
    assert len(rows) == 3 and rows[-1].name == "answer"
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"


async def test_planning_timeout_closes_call_and_preserves_budget_for_local_answer(harness):
    search, model = install(harness)
    harness.settings.web_search_agent_timeout_seconds = 0.5
    model.plan_blocked = True
    request = await harness.submit(content="최신 Python 문서를 검색해줘")
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed" and not search.search_calls
    rows = await steps(harness, request["id"])
    assert rows[0].usage_basis == "waived" and rows[0].input_tokens == 0
    assert rows[0].budget_tokens == 356 and rows[-1].name == "answer"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens


async def test_final_answer_usage_survives_recovery_after_step_commit(harness):
    from backend.app.runtime.steps import StepService

    search, model = install(harness)
    request = await harness.submit(content="검색 없는 질문")
    job = await harness.worker.claim()
    step_id = await StepService(harness.database, harness.settings).start(
        job, kind="llm", name="answer", prompt_tokens=job["prompt_tokens"], max_output_tokens=64
    )
    await harness.service.append_chunk(job["id"], "저장된 부분 답변")
    await StepService(harness.database, harness.settings).close(
        job, step_id, status="completed", input_tokens=job["prompt_tokens"], output_tokens=12
    )
    await harness.worker.recover()
    saved = await snapshot(harness.database, request["id"])
    assert saved.reservation.input_tokens == job["prompt_tokens"]
    assert saved.reservation.output_tokens == saved.assistant.token_count == 12
    assert saved.run.status == "failed" and saved.run.error_code == "worker_interrupted"
    assert not model.calls and not search.search_calls
