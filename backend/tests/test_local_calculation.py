"""로컬 계산 판단의 입력 경계·정산·중단과 최종 답변 문맥을 검증한다."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable, ToolCall
from backend.app.llm.providers.mock import MockProvider
from backend.app.models import GenerationRun, GenerationStep, Message
from backend.app.runtime.cancellation import GenerationCancelled
from backend.app.runtime.contracts import ModelExecution
from backend.app.runtime.steps import StepLimitExceeded
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.network_mode import NetworkModeService
from backend.app.tools.calculation import planning, service
from backend.tests.test_generations import balance, snapshot
from backend.tests.test_generations import harness as harness
from backend.tests.test_web_search_generation import FakeSearchProvider, finish_task

QUESTION = "정가 12500원에서 12% 할인하고 배송비 1800원을 더한 총액을 계산해줘."
EXPRESSION = "12500 * (1 - 0.12) + 1800"
SOURCE = "values = [3, 7, 9]\nprint(sum(item for item in values if item > 5))"


def tool_call(name="calculate", arguments=None):
    return ToolCall(
        "calculation_1", name,
        json.dumps(arguments if arguments is not None else {"expression": EXPRESSION}),
    )


class CalculationProvider(MockProvider):
    """도구 응답·실제 사용량·중단 위치를 제어하며 외부 모델을 호출하지 않는다."""

    def __init__(self, settings):
        super().__init__(settings, delay_seconds=0)
        self.action = tool_call()
        self.tool_input_count = 100
        self.final_input_count = 300
        self.usage_input = 100
        self.usage_output = 20
        self.finish_reason = "tool_calls"
        self.initial_received = 3
        self.tool_calls_override = None
        self.plan_calls = []
        self.tool_counts = []
        self.input_counts = []
        self.answer_calls = []
        self.plan_started = asyncio.Event()
        self.plan_release = asyncio.Event()
        self.plan_closed = asyncio.Event()
        self.plan_blocked = False
        self.omit_final = False

    async def count_tools(self, messages, options, tools):
        if tools[0]["function"]["name"] == "ask_user_question":
            return await self.count_input(messages, options)
        self.tool_counts.append((copy.deepcopy(messages), options.model_copy(), tools))
        return self.tool_input_count

    async def count_input(self, messages, options):
        self.input_counts.append((copy.deepcopy(messages), options.model_copy()))
        return self.final_input_count

    async def stream_tools(self, messages, options, tools):
        if tools[0]["function"]["name"] == "ask_user_question":
            async for delta in self.stream(messages, options):
                yield delta
            return
        self.plan_calls.append((copy.deepcopy(messages), options.model_copy(), tools))
        self.plan_started.set()
        try:
            yield ProviderDelta(received_output_tokens=self.initial_received)
            if self.plan_blocked:
                await self.plan_release.wait()
            if not self.omit_final:
                yield ProviderDelta(
                    final=True, input_tokens=self.usage_input, output_tokens=self.usage_output,
                    received_output_tokens=self.usage_output, finish_reason=self.finish_reason,
                    tool_calls=(self.action,) if self.tool_calls_override is None
                    else self.tool_calls_override,
                )
        finally:
            self.plan_closed.set()

    async def stream(self, messages, options):
        self.answer_calls.append((copy.deepcopy(messages), options.model_copy()))
        yield ProviderDelta(text="검증 결과에 따른 최종 답변", received_output_tokens=3)
        yield ProviderDelta(
            final=True, input_tokens=self.final_input_count, output_tokens=3,
            received_output_tokens=3, finish_reason="stop",
        )


class FakeLedger:
    """DB 없이 단계 시작·종료 호출과 남은 허용량만 관찰한다."""

    def __init__(self):
        self.started = []
        self.closed = []
        self.available_tokens = 4096
        self.reject_start = False

    async def remaining(self, job):
        return self.available_tokens

    async def start(self, job, **kwargs):
        if self.reject_start:
            raise StepLimitExceeded
        identifier = uuid4()
        self.started.append((identifier, kwargs))
        return identifier

    async def close(self, job, identifier, **kwargs):
        self.closed.append((identifier, kwargs))
        return True


class FakeDatabase:
    """원 질문과 단계 수 조회만 허용하며 문맥 저장은 별도 관찰 함수로 대체한다."""

    def __init__(self):
        self.question = QUESTION
        self.step_count = 0
        self.run = SimpleNamespace(
            user_message_id=uuid4(), status="running", cancel_requested=False,
            prompt_tokens=80, max_output_tokens=64,
        )
        self.commits = 0

    @asynccontextmanager
    async def session(self):
        yield self

    async def get(self, model, identifier):
        if model is GenerationRun:
            return self.run
        if model is Message:
            return SimpleNamespace(content=self.question)
        pytest.fail("허용하지 않은 DB 조회입니다.")

    async def scalar(self, statement):
        if statement.column_descriptions[0].get("entity") is GenerationRun:
            return self.run
        return self.step_count

    async def commit(self):
        self.commits += 1


@pytest.fixture
def setup_calculation(monkeypatch):
    settings = Settings(
        _env_file=None, llm_backend="mock", llm_context_window=4096,
        llm_base_url="http://127.0.0.1:8080/v1", database_enabled=False,
        database_url=None, migration_database_url=None,
        web_search_provider="disabled", web_search_api_key=None,
    )
    provider = CalculationProvider(settings)
    database = FakeDatabase()
    ledger = FakeLedger()
    execution = ModelExecution(database, provider, settings)
    job = {
        "id": uuid4(), "user_id": uuid4(), "workspace_id": uuid4(),
        "conversation_id": uuid4(), "messages": [
            {"role": "system", "content": "현재 질문에 한국어로 답하세요."},
            {"role": "user", "content": QUESTION},
        ],
        "options": {"thinking": False, "max_tokens": 64}, "prompt_tokens": 80,
        "memory_dependencies": {}, "context_compaction_needed": False,
        "network_mode": "local", "network_revision": 0, "web_search_mode": "off",
    }
    monkeypatch.setattr(planning, "StepService", lambda *args: ledger)
    monkeypatch.setattr(service, "StepService", lambda *args: ledger)

    async def permitted(*args):
        return True

    monkeypatch.setattr(
        service, "Repository", lambda *args: SimpleNamespace(get_conversation=permitted),
    )
    monkeypatch.setattr(service, "check_dependencies", permitted)
    return SimpleNamespace(
        settings=settings, provider=provider, database=database,
        ledger=ledger, execution=execution, job=job,
    )


@pytest.fixture
async def cancellation():
    event = asyncio.Event()
    task = asyncio.create_task(event.wait())
    try:
        yield event, task
    finally:
        await finish_task(task)


async def test_planning_records_one_confirmed_call_and_reserves_final_answer(
    setup_calculation, cancellation,
):
    setup = setup_calculation
    call = await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    assert call == setup.provider.action
    assert len(setup.provider.plan_calls) == 1
    messages, options, _ = setup.provider.plan_calls[0]
    assert any(QUESTION in message.content for message in messages)
    assert options.thinking is False
    assert len(setup.ledger.started) == len(setup.ledger.closed) == 1
    started = setup.ledger.started[0][1]
    closed = setup.ledger.closed[0][1]
    assert started["name"] == "calculation_plan"
    assert started["prompt_tokens"] == 100
    assert started["keep_tokens"] >= setup.job["prompt_tokens"] + 64
    assert closed["status"] == "completed"
    assert closed["input_tokens"] == 100
    assert closed["output_tokens"] == 20
    assert setup.provider.plan_closed.is_set()


@pytest.mark.parametrize("call", [
    tool_call(arguments={"expression": EXPRESSION, "extra": True}),
    tool_call(arguments={"expression": ""}),
    tool_call(arguments={"expression": 42}),
    tool_call(name="web_search", arguments={"query": "개인 데이터"}),
    tool_call(name="skip_calculation", arguments={"reason": "already_verified"}),
    ToolCall("bad id", "calculate", '{"expression":"1+2"}'),
    ToolCall("calculation_1", "calculate", "{invalid"),
])
def test_untrusted_tool_arguments_are_rejected_before_local_execution(call):
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(call, QUESTION)


def test_python_tool_cannot_replace_user_code_with_different_source():
    question = f"다음 Python 코드의 출력만 알려줘.\n```python\n{SOURCE}\n```"
    changed = tool_call("trace_python", {"source": "print(999)"})
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(changed, question)
    verified = planning.resolve_tool(tool_call("trace_python", {"source": SOURCE}), question)
    assert verified["verified"] is True
    assert verified["result"].strip() == "16"


@pytest.mark.parametrize("source, expected", [
    ("print(-7 // 3)", "-3"),
    ("values = [-5, 4]\nprint([value // 2 for value in values])", "[-3, 2]"),
])
def test_verified_python_floor_division_includes_semantics(source, expected):
    question = f"다음 Python 코드의 출력을 알려줘.\n```python\n{source}\n```"
    result = planning.resolve_tool(tool_call("trace_python", {"source": source}), question)
    assert result["verified"] is True
    assert result["result"].strip() == expected
    assert result["semantics"]


@pytest.mark.parametrize("source", [
    "print(-7 / 2)",
    "print('//')",
    SOURCE,
])
def test_python_trace_without_floor_operator_has_no_semantics(source):
    question = f"다음 Python 코드의 출력을 알려줘.\n```python\n{source}\n```"
    result = planning.resolve_tool(tool_call("trace_python", {"source": source}), question)
    assert result["verified"] is True
    assert "semantics" not in result


@pytest.mark.parametrize("question, expression", [
    (QUESTION, EXPRESSION),
    ("13을 4로 나눈 정수 몫을 계산해줘.", "13 // 4"),
])
def test_numeric_calculation_has_no_python_semantics(question, expression):
    result = planning.resolve_tool(tool_call(arguments={"expression": expression}), question)
    assert result["verified"] is True
    assert "semantics" not in result


def test_skipped_calculation_has_no_python_semantics():
    result = planning.resolve_tool(
        tool_call("skip_calculation", {"reason": "unsupported"}), QUESTION,
    )
    assert result["verified"] is False
    assert "semantics" not in result


def test_python_tool_cannot_verify_only_a_truncated_part_of_a_code_block():
    question = "다음 Python 코드의 전체 출력을 알려줘.\n```python\nprint(1)\nprint(2)\n```"
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(tool_call("trace_python", {"source": "print(2)"}), question)


def test_python_tool_cannot_discard_an_invalid_leading_statement():
    question = "다음 Python 코드의 출력 결과를 알려줘.\nbroken = [1,\nprint(2)"
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(tool_call("trace_python", {"source": "print(2)"}), question)


@pytest.mark.parametrize(("question", "expected"), [
    ("물 2.5L에서 750mL를 썼어. 남은 물의 양은?", True),
    ("다음 코드를 실행하면 화면에 표시되는 값은?\nprint(17 // 4)", True),
    ("다음 코드의 설명만 영어로 번역해줘.\nprint(17 // 4)", False),
    ("다음 코드가 하는 동작을 설명해줘.\nprint(17 // 4)", False),
])
def test_calculation_gate_handles_varied_units_and_code_intent(question, expected):
    assert planning.should_calculate(question) is expected


@pytest.mark.parametrize("expression", ["12800", "47900", "47900 + 0", "12500 + 0"])
def test_calculation_rejects_guessed_constants_and_single_input_padding(expression):
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(tool_call(arguments={"expression": expression}), QUESTION)


def test_calculation_rejects_a_real_input_padded_with_zero():
    question = "18000원짜리 물건 4개에서 10% 할인한 총액을 계산해줘."
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.resolve_tool(tool_call(arguments={"expression": "18000 + 0"}), question)


@pytest.mark.parametrize(("question", "expression", "expected"), [
    (QUESTION, "12500 * (1 - 12 / 100) + 1800", "12800"),
    ("25000원을 12% 할인하고 다시 5% 할인한 금액은?", "25000 * 0.88 * 0.95", "20900"),
    ("물 2.5L에서 750mL를 썼어. 남은 양은 몇 mL야?", "2.5 * 1000 - 750", "1750"),
    ("물 2.5L에서 750mL를 썼어. 남은 양은 몇 L야?", "2.5 - 0.75", "1.75"),
])
def test_calculation_accepts_explicit_percentages_and_unit_conversions(
    question, expression, expected,
):
    result = planning.resolve_tool(tool_call(arguments={"expression": expression}), question)
    assert result["verified"] is True
    assert result["input"] == expression
    assert result["result"] == expected


@pytest.mark.parametrize(("counted", "actual_input", "actual_output", "missing_final"), [
    (100, 101, 20, False),
    (100, None, 20, False),
    (100, True, 20, False),
    (100, 100, None, False),
    (100, 100, True, False),
    (100, 100, -1, False),
    (100, 100, 4097, False),
    (100, 100, 20, True),
])
async def test_unconfirmed_planning_usage_cannot_be_reported_as_completed(
    setup_calculation, cancellation, counted, actual_input, actual_output, missing_final,
):
    setup = setup_calculation
    setup.provider.tool_input_count = counted
    setup.provider.usage_input = actual_input
    setup.provider.usage_output = actual_output
    setup.provider.omit_final = missing_final
    with pytest.raises(ProviderUnavailable):
        await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    assert len(setup.ledger.closed) == 1
    assert setup.ledger.closed[0][1]["status"] == "failed"
    assert setup.ledger.closed[0][1]["input_tokens"] is None
    assert setup.ledger.closed[0][1]["output_tokens"] is None
    assert setup.provider.plan_closed.is_set()


async def test_observed_usage_above_final_usage_is_never_confirmed(
    setup_calculation, cancellation,
):
    setup = setup_calculation
    setup.provider.initial_received = 50
    with pytest.raises(ProviderUnavailable):
        await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    closed = setup.ledger.closed[0][1]
    assert closed["status"] == "failed"
    assert closed["received_output_tokens"] == 50
    assert closed["input_tokens"] is None
    assert closed["output_tokens"] is None


@pytest.mark.parametrize("calls", [(), (tool_call(), tool_call())])
async def test_bad_tool_call_count_preserves_valid_confirmed_usage(
    setup_calculation, cancellation, calls,
):
    setup = setup_calculation
    setup.provider.tool_calls_override = calls
    with pytest.raises(ProviderUnavailable):
        await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    closed = setup.ledger.closed[0][1]
    assert closed["status"] == "failed"
    assert closed["input_tokens"] == 100
    assert closed["output_tokens"] == 20
    assert closed["received_output_tokens"] == 20


@pytest.mark.parametrize("counted", [True, 0, -1, 4096])
async def test_invalid_or_excessive_planning_input_never_starts_generation(
    setup_calculation, cancellation, counted,
):
    setup = setup_calculation
    setup.provider.tool_input_count = counted
    with pytest.raises(ProviderUnavailable):
        await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    assert setup.provider.plan_calls == []
    assert setup.ledger.started == []


async def test_cancel_during_planning_closes_stream_and_keeps_observed_usage(
    setup_calculation, cancellation,
):
    setup = setup_calculation
    setup.provider.plan_blocked = True
    task = asyncio.create_task(
        planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    )
    try:
        await asyncio.wait_for(setup.provider.plan_started.wait(), 1)
        await asyncio.sleep(0)
        cancellation[0].set()
        with pytest.raises(GenerationCancelled):
            await asyncio.wait_for(task, 1)
    finally:
        await finish_task(task)
    assert setup.provider.plan_closed.is_set()
    assert len(setup.ledger.closed) == 1
    closed = setup.ledger.closed[0][1]
    assert closed["status"] == "cancelled"
    assert closed["received_output_tokens"] == 3
    assert closed["input_tokens"] is None
    assert closed["output_tokens"] is None


async def test_planning_budget_rejection_prevents_provider_stream(setup_calculation, cancellation):
    setup = setup_calculation
    setup.ledger.reject_start = True
    with pytest.raises(StepLimitExceeded):
        await planning.plan(setup.execution, setup.job, QUESTION, cancellation[1])
    assert setup.provider.plan_calls == []
    assert setup.ledger.closed == []


@pytest.fixture
def context_storage(monkeypatch):
    stored = []
    original = service.CalculationService._store_context

    async def store(self, job, result, cancellation):
        updated = await original(self, job, result, cancellation)
        stored.append((
            [ChatMessage.model_validate(value) for value in updated["messages"]],
            GenerationOptions.model_validate(updated["options"]), updated["prompt_tokens"],
        ))
        return updated

    monkeypatch.setattr(service.CalculationService, "_store_context", store)
    return stored


@pytest.mark.parametrize("thinking", [False, True])
async def test_direct_python_trace_uses_no_model_plan_and_only_two_available_slots(
    setup_calculation, cancellation, context_storage, thinking,
):
    setup = setup_calculation
    question = f"다음 코드를 실행하면 화면에 표시되는 값은?\n```python\n{SOURCE}\n```"
    setup.database.question = question
    setup.job["messages"][-1]["content"] = question
    setup.job["options"]["thinking"] = thinking
    setup.database.step_count = setup.settings.generation_max_steps - 2
    direct = planning.direct_python_call(question)
    assert direct is not None
    assert direct.name == "trace_python"
    assert json.loads(direct.arguments)["source"] == SOURCE
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert setup.provider.tool_counts == setup.provider.plan_calls == []
    assert [details["name"] for _, details in setup.ledger.started] == ["calculate"]
    assert setup.ledger.closed[0][1]["status"] == "completed"
    assert updated["options"]["thinking"] is thinking
    assert updated["prompt_tokens"] == setup.provider.final_input_count
    messages = context_storage[0][0]
    reference = next(
        json.loads(message.content.split("\n", 1)[1])["local_calculation"]
        for message in messages if "local_calculation JSON" in message.content
    )
    assert reference["verified"] is True
    assert reference["input"] == SOURCE
    assert reference["result"] == "16\n"


async def test_direct_python_trace_never_ignores_invalid_leading_code(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    question = "다음 Python 코드의 출력 결과를 알려줘.\nbroken = [1,\nprint(2)"
    setup.database.question = question
    setup.job["messages"][-1]["content"] = question
    with pytest.raises((ValueError, ProviderUnavailable)):
        planning.direct_python_call(question)
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert setup.provider.tool_counts == setup.provider.plan_calls == []
    assert setup.ledger.started == []
    reference = next(
        json.loads(message["content"].split("\n", 1)[1])["local_calculation"]
        for message in updated["messages"] if "local_calculation JSON" in message["content"]
    )
    assert reference["verified"] is False
    assert reference["result"] == ""


@pytest.mark.parametrize("thinking", [False, True])
async def test_verified_context_is_counted_again_without_changing_thinking(
    setup_calculation, cancellation, context_storage, thinking,
):
    setup = setup_calculation
    setup.job["options"]["thinking"] = thinking
    original = copy.deepcopy(setup.job)
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert setup.job == original
    assert updated["options"]["thinking"] is thinking
    assert updated["prompt_tokens"] == setup.provider.final_input_count
    assert setup.provider.input_counts
    assert len(context_storage) == 1
    messages, options, tokens = context_storage[0]
    assert options.thinking is thinking
    assert tokens == setup.provider.final_input_count
    assert messages == setup.provider.input_counts[-1][0]
    serialized = json.dumps(updated["messages"], ensure_ascii=False)
    assert "12800" in serialized
    assert '"verified": true' in serialized or '\\"verified\\": true' in serialized
    assert [details["name"] for _, details in setup.ledger.started] == [
        "calculation_plan", "calculate",
    ]


async def test_insufficient_step_slots_skip_planning_and_do_not_claim_verification(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    setup.database.step_count = setup.settings.generation_max_steps - 2
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert setup.provider.plan_calls == []
    assert setup.ledger.started == []
    assert context_storage
    assert '"verified": false' in json.dumps(updated["messages"], ensure_ascii=False).replace(
        '\\"', '"'
    )


async def test_failed_plan_never_injects_a_verified_result(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    setup.provider.action = tool_call(arguments={"expression": "1 / 0"})
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    serialized = json.dumps(updated["messages"], ensure_ascii=False).replace('\\"', '"')
    assert '"verified": false' in serialized
    assert '"verified": true' not in serialized
    assert updated["prompt_tokens"] == setup.provider.final_input_count


async def test_invalid_usage_falls_back_without_running_the_local_tool(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    setup.provider.usage_input = 101
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert [details["name"] for _, details in setup.ledger.started] == ["calculation_plan"]
    assert setup.ledger.closed[0][1]["status"] == "failed"
    assert setup.provider.input_counts
    reference = next(
        json.loads(message["content"].split("\n", 1)[1])["local_calculation"]
        for message in updated["messages"] if "local_calculation JSON" in message["content"]
    )
    assert reference["verified"] is False
    assert not reference["result"]


async def test_remaining_budget_reduces_only_output_and_recounts_final_context(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    setup.ledger.available_tokens = setup.provider.final_input_count + 17
    updated = await service.CalculationService(setup.execution).prepare(
        setup.job, cancellation[1],
    )
    assert updated["prompt_tokens"] == 300
    assert updated["options"] == {"thinking": False, "max_tokens": 17}
    assert setup.database.run.prompt_tokens == 300
    assert setup.database.run.max_output_tokens == 17
    assert setup.database.commits == 1


async def test_final_context_that_cannot_fit_fails_before_answer_generation(
    setup_calculation, cancellation, context_storage,
):
    setup = setup_calculation
    setup.provider.final_input_count = setup.settings.llm_context_window
    with pytest.raises(StepLimitExceeded):
        await service.CalculationService(setup.execution).prepare(setup.job, cancellation[1])
    assert not context_storage
    assert not setup.provider.answer_calls


async def generation_steps(harness, run_id):
    async with harness.database.session() as session:
        rows = list((await session.scalars(
            select(GenerationStep)
            .where(GenerationStep.generation_id == UUID(str(run_id)))
            .order_by(GenerationStep.sequence)
        )).all())
        session.expunge_all()
        return rows


@pytest.mark.postgres
@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize(("question", "direct"), [
    (QUESTION, False),
    (f"다음 Python 코드의 출력 결과를 알려줘.\n```python\n{SOURCE}\n```", True),
])
async def test_local_calculation_uses_no_search_and_settles_all_confirmed_calls_once(
    harness, thinking, question, direct,
):
    provider = CalculationProvider(harness.settings)
    search = FakeSearchProvider()
    harness.provider = harness.service.provider = provider
    harness.service.search_provider = search
    harness.service.network_mode = NetworkModeService(harness.database, harness.settings, search)
    await harness.grant()
    before = await balance(harness.database, harness.member)
    request = await harness.submit(
        harness.member, content=question, network_mode="local", web_search="off",
        options=GenerationOptions(thinking=thinking, max_tokens=64),
    )
    pending = await balance(harness.database, harness.member)
    assert pending.used_tokens == before.used_tokens
    assert pending.reserved_tokens == 0
    await harness.execute_next()
    saved = await snapshot(harness.database, request["id"])
    steps = await generation_steps(harness, request["id"])
    assert saved.run.status == "completed"
    assert search.check_calls == 0
    assert search.search_calls == []
    assert [step.name for step in steps] == (
        ["calculate", "answer"] if direct else ["calculation_plan", "calculate", "answer"]
    )
    assert len(provider.plan_calls) == (0 if direct else 1)
    assert provider.answer_calls[0][1].thinking is thinking
    assert saved.run.prompt_tokens == provider.final_input_count
    assert saved.reservation.input_tokens == (0 if direct else 100) + provider.final_input_count
    assert saved.reservation.output_tokens == (0 if direct else 20) + 3
    assert saved.reservation.input_tokens == sum(step.input_tokens for step in steps)
    assert saved.reservation.output_tokens == sum(step.output_tokens for step in steps)
    await harness.service.finish(UUID(request["id"]))
    after = await balance(harness.database, harness.member)
    assert after.used_tokens - before.used_tokens == (
        saved.reservation.input_tokens + saved.reservation.output_tokens
    )
