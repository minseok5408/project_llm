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
from backend.app.tools.web_search.context import (
    build_search_fallback_messages,
    search_unavailable_notice,
)
from backend.app.tools.web_search.planning import build_planning_messages, query_entries
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
        self.calculation_calls = []
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
        if tools[0]["function"]["name"] == "calculate":
            self.calculation_calls.append(messages)
            yield ProviderDelta(
                final=True,
                input_tokens=100,
                output_tokens=20,
                received_output_tokens=20,
                finish_reason="tool_calls",
                tool_calls=(ToolCall("calculate_1", "calculate", '{"expression":"2 + 2"}'),),
            )
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
    ("content", "calculation"),
    [
        pytest.param("안녕", False, id="greeting"),
        pytest.param("파이썬 리스트를 설명해줘", False, id="basic-concept"),
        pytest.param("'고마워'를 영어로 번역해줘", False, id="translation"),
        pytest.param("2 + 2는 얼마야?", True, id="calculation"),
    ],
)
async def test_auto_without_web_need_skips_search_planning_and_steps(harness, content, calculation):
    search, model = install(harness)
    request = await harness.submit(content=content, network_mode="auto", web_search="auto")
    await harness.execute_next()

    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == saved.assistant.status == "completed"
    assert saved.run.network_mode == saved.run.web_search_mode == "auto"
    assert search.check_calls == 0 and search.search_calls == []
    assert model.plan_calls == []
    assert len(model.calculation_calls) == int(calculation)
    assert len(model.calls) == 1
    assert await search_record(harness, request["id"]) is None
    rows = await steps(harness, request["id"])
    assert [row.name for row in rows] == (
        ["calculation_plan", "calculate", "answer"] if calculation else ["answer"]
    )
    assert all(row.status == "completed" for row in rows)
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + (100 if calculation else 0)
    assert saved.reservation.output_tokens == saved.assistant.token_count + (
        20 if calculation else 0
    )


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


@pytest.mark.parametrize("account", ["member", "system"])
async def test_semantic_skip_has_no_external_io_and_settles_planning_once(harness, account):
    search, model = install(harness)
    await harness.grant()
    actor = getattr(harness, account)
    model.plans = [ToolCall("skip_1", "finish_search", '{"reason":"not_needed"}')]
    model.blocked = True
    before = await balance(harness.database, actor)
    request = await harness.submit(
        actor, content="오늘은 기분이 좋아", network_mode="auto", web_search="auto"
    )
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        during = await balance(harness.database, actor)
        assert during.used_tokens == before.used_tokens and during.reserved_tokens == 0
        model.release.set()
        await asyncio.wait_for(task, 5)
    finally:
        await finish_task(task)
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed"
    assert search.check_calls == 0 and search.search_calls == []
    assert await search_record(harness, request["id"]) is None
    rows = await steps(harness, request["id"])
    assert [row.name for row in rows] == ["search_plan", "answer"]
    assert saved.reservation.input_tokens == sum(row.input_tokens for row in rows)
    assert saved.reservation.output_tokens == sum(row.output_tokens for row in rows)
    assert all(
        search_unavailable_notice(reason) not in model.calls[0][0]
        for reason in ("no_query", "no_results")
    )
    await harness.service.finish(UUID(request["id"]))
    after = await balance(harness.database, actor)
    assert after.used_tokens - before.used_tokens == (
        saved.reservation.input_tokens + saved.reservation.output_tokens
    )
    assert after.unlimited is (account == "system")
    if account == "system":
        assert after.remaining_tokens is None and after.budget_id is None


@pytest.mark.parametrize("restriction", ["search_off", "forced_local"])
@pytest.mark.parametrize(
    ("content", "calculation"),
    [("오늘은 기분이 좋아", False), ("오늘 2 + 2는 얼마야?", True)],
)
async def test_restricted_search_keeps_self_contained_answer_and_local_calculation(
    harness, restriction, content, calculation
):
    search, model = install(harness)
    request = await harness.submit(
        content=content,
        options=GenerationOptions(max_tokens=512),
        network_mode="local" if restriction == "forced_local" else "auto",
        web_search="off" if restriction == "search_off" else "auto",
    )
    await harness.execute_next()

    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed" and saved.user_message.content == content
    assert search.check_calls == 0 and search.search_calls == [] and model.plan_calls == []
    assert len(model.calls) == 1 and bool(model.calculation_calls) is calculation
    assert (await search_record(harness, request["id"])).reason == restriction
    messages, options = model.calls[0]
    assert search_unavailable_notice(restriction) in messages
    prefix = content + "\n\n[Server-verified search status for this turn]\n"
    assert messages[-1].content.startswith(prefix)
    metadata = json.loads(messages[-1].content[len(prefix) :].split("\n", 1)[0])
    assert metadata == {
        "reason": restriction,
        "evidence_available": False,
        "answer_mode": "self_contained_or_notice",
    }
    assert saved.run.prompt_tokens == await model.count_input(messages, options)
    # 모의 모델의 문장 의미 대신 로컬 계산·정상 답변 실행과 실제 입력 계약을 검사한다.
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + (100 if calculation else 0)


@pytest.mark.parametrize("reason", ["no_query", "sufficient"])
async def test_missing_target_and_false_sufficiency_do_not_check_connection(harness, reason):
    search, model = install(harness)
    model.plans = [ToolCall("finish_1", "finish_search", json.dumps({"reason": reason}))]
    request = await harness.submit(content="그 회사 최신 뉴스를 검색해줘")
    await harness.execute_next()
    assert search.check_calls == 0 and search.search_calls == []
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed"
    record = await search_record(harness, request["id"])
    assert record.reason == ("no_query" if reason == "no_query" else "planning_failed")
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 100
    assert saved.reservation.output_tokens == saved.assistant.token_count + 20


async def test_prohibited_current_lookup_adds_notice_without_planning_or_external_io(harness):
    search, model = install(harness)
    request = await harness.submit(
        content="검색 없이 Python 최신 버전을 확인 가능한 범위만 알려줘",
        options=GenerationOptions(max_tokens=512),
        network_mode="auto",
        web_search="auto",
    )
    await harness.execute_next()
    assert search.check_calls == 0 and search.search_calls == [] and model.plan_calls == []
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed"
    assert (await search_record(harness, request["id"])).reason == "search_off"
    assert [row.name for row in await steps(harness, request["id"])] == ["answer"]
    assert search_unavailable_notice("search_off") in model.calls[0][0]


async def test_explicit_auto_search_cannot_be_declined_as_not_needed(harness):
    search, model = install(harness)
    model.plans = [ToolCall("skip_1", "finish_search", '{"reason":"not_needed"}')]
    request = await harness.submit(
        content="삼각형의 정의를 웹검색해줘", network_mode="auto", web_search="auto"
    )
    await harness.execute_next()
    assert search.check_calls == 0 and search.search_calls == []
    assert (await search_record(harness, request["id"])).reason == "planning_failed"
    saved = await snapshot(harness.database, request["id"])
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 100


async def test_elliptical_followup_gets_planning_budget_and_bounded_query(harness):
    search, model = install(harness)
    await harness.grant()
    await harness.submit(harness.member, content="오늘 서울 날씨 알려줘", web_search="off")
    await harness.execute_next()
    model.plans = [
        ToolCall(
            "followup_1",
            "web_search",
            json.dumps({"terms": [{"message": 0, "text": "부산"}, {"message": 1, "text": "날씨"}]}),
        ),
        ToolCall("finish_1", "finish_search", '{"reason":"sufficient"}'),
    ]
    request = await harness.submit(
        harness.member, content="그럼 부산은?", network_mode="auto", web_search="auto"
    )
    await harness.execute_next()
    assert search.search_calls == ["부산 날씨"]
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "completed"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens + 200
    review = json.loads(model.plan_calls[-1][-1].content)["sources"][0]
    assert review["url"] == search.results[0].url and review["retrieved_at"]


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


async def test_low_budget_stops_without_planning_or_unprotected_answer(harness):
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
    assert not search.search_calls and not model.plan_calls and not model.calls
    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "failed" and saved.run.error_code == "step_limit"
    assert saved.reservation.status == "released"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0


async def test_fallback_limit_charges_only_confirmed_planner_usage(harness):
    search, model = install(harness)
    await harness.grant()
    content = "그 제품의 최신 버전을 검색해줘"
    model.plans = [ToolCall("missing_subject", "finish_search", '{"reason":"no_query"}')]
    request = await harness.submit(harness.member, content=content)
    messages = compose_context([], content)
    harness.settings.llm_max_history_chars = (
        min(
            sum(
                len(message.content) for message in build_search_fallback_messages(messages, reason)
            )
            for reason in ("no_query", "step_limit")
        )
        - 1
    )
    planning = build_planning_messages(
        query_entries({"messages": [message.model_dump() for message in messages]}, content),
        [],
        [],
    )
    assert (
        sum(len(message.content) for message in planning) < harness.settings.llm_max_history_chars
    )

    await asyncio.wait_for(harness.execute_next(), timeout=5)

    saved = await snapshot(harness.database, request["id"])
    assert saved.run.status == "failed" and saved.run.error_code == "step_limit"
    assert len(model.plan_calls) == 1 and model.calls == []
    assert search.check_calls == 0 and search.search_calls == []
    assert saved.reservation.input_tokens == 100 and saved.reservation.output_tokens == 20
    assert saved.reservation.usage_basis == "provider"
    assert [row.name for row in await steps(harness, request["id"])] == ["search_plan"]
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == 120 and current.reserved_tokens == 0


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
