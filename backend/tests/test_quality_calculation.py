"""실제 답변 평가에 로컬 검증 입력·준비 비용·실패 상태가 빠지지 않는지 확인한다."""

import json
from types import SimpleNamespace

import pytest

from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable, ToolCall
from backend.app.schemas import GenerationOptions
from backend.evaluation.runner import prepare_report, run_model
from backend.evaluation.schema import DEFAULT_DATASET, load_dataset
from backend.evaluation.scoring import canonical_hash
from scripts.evaluate_answers import configured, parser


class CalculationProvider:
    """계획과 최종 답변의 서로 다른 입력량을 반환하는 독립 공급자 대역."""

    def __init__(self, *, plan_usage=120, skip=False, fail=False):
        self.plan_usage = plan_usage
        self.skip = skip
        self.fail = fail
        self.plan_calls = []
        self.answer_calls = []

    async def status(self):
        return None

    async def count_tools(self, messages, options, tools):
        return 120

    async def stream_tools(self, messages, options, tools):
        self.plan_calls.append((messages, options, tools))
        yield ProviderDelta(received_output_tokens=3)
        if self.fail:
            raise ProviderUnavailable("외부 오류 비밀 표식 PRIVATE_PROVIDER_DETAIL")
        call = (
            ToolCall("plan_1", "skip_calculation", '{"reason":"ambiguous"}')
            if self.skip
            else ToolCall("plan_1", "calculate", '{"expression":"18000*3*0.85+2500"}')
        )
        yield ProviderDelta(
            input_tokens=self.plan_usage,
            output_tokens=10,
            final=True,
            finish_reason="tool_calls",
            tool_calls=(call,),
        )

    async def count_input(self, messages, options):
        return 200

    async def stream(self, messages, options):
        self.answer_calls.append((messages, options))
        yield ProviderDelta(text="18,000 × 3 × 0.85 + 2,500 = 48,400원", received_output_tokens=5)
        yield ProviderDelta(input_tokens=200, output_tokens=6, final=True, finish_reason="stop")


async def evaluate(provider, *, thinking=False, enabled=True, case_id="calculation_discount"):
    dataset = load_dataset()
    settings = configured(parser().parse_args(["validate"]))
    options = GenerationOptions(thinking=thinking, max_tokens=512)
    report, prompts = prepare_report(
        dataset,
        DEFAULT_DATASET,
        settings,
        options,
        model=True,
        selected=[case_id],
        local_calculation=enabled,
    )
    await run_model(report, prompts, dataset, settings, options, provider=provider)
    return report, report["cases"][0]


@pytest.mark.parametrize("thinking", [False, True])
async def test_shared_calculation_input_and_usage_are_preserved(thinking):
    provider = CalculationProvider()
    report, case = await evaluate(provider, thinking=thinking)
    preparation = case["calculation"]
    assert preparation["status"] == "verified"
    assert preparation["evidence"]["result"] == "48400"
    assert preparation["plan"]["input_tokens"] == 120
    assert preparation["plan"]["output_tokens"] == 10
    assert case["execution"]["input_tokens"] == 200
    assert case["execution"]["output_tokens"] == 6
    assert provider.plan_calls[0][1].thinking is False
    assert provider.answer_calls[0][1].thinking is thinking
    assert "local_calculation" in case["input_messages"][-2]["content"]
    assert case["input_sha256"] == canonical_hash(case["input_messages"])
    assert preparation["plan"]["input_sha256"] == canonical_hash(
        preparation["plan"]["input_messages"]
    )
    assert "score_anchors" not in json.dumps(provider.plan_calls[0][2])
    assert report["quality_status"] == "pending_review"


@pytest.mark.parametrize("plan_usage", [119, None, True])
async def test_bad_preparation_usage_cannot_pass_with_a_completed_answer(plan_usage):
    report, case = await evaluate(CalculationProvider(plan_usage=plan_usage))
    assert case["answer"].endswith("48,400원")
    assert case["execution"]["usage_status"] == "confirmed"
    assert case["execution"]["status"] == "failed"
    assert case["execution"]["error"] == "calculation_preparation_failed"
    assert case["calculation"]["plan"]["usage_status"] == "invalid"
    assert case["calculation"]["plan"]["input_tokens"] is None
    assert case["calculation"]["evidence"]["verified"] is False
    assert report["quality_status"] == "execution_failed"


async def test_partial_preparation_preserves_received_usage_without_leaking_error():
    report, case = await evaluate(CalculationProvider(fail=True))
    assert case["calculation"]["plan"]["usage_status"] == "received_only"
    assert case["calculation"]["plan"]["received_output_tokens"] == 3
    assert case["calculation"]["plan"]["output_tokens"] is None
    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(report)
    assert case["execution"]["status"] == "failed"


async def test_intentional_skip_is_recorded_as_unverified():
    _, case = await evaluate(CalculationProvider(skip=True))
    assert case["calculation"]["status"] == "skipped"
    assert case["calculation"]["evidence"]["verified"] is False
    assert case["calculation"]["plan"]["input_tokens"] == 120
    assert case["execution"]["status"] == "completed"


async def test_python_trace_uses_exact_source_without_a_model_plan():
    provider = CalculationProvider()
    _, case = await evaluate(provider, case_id="code_trace")
    assert provider.plan_calls == []
    assert case["calculation"]["plan"] is None
    assert case["calculation"]["status"] == "verified"
    assert case["calculation"]["evidence"]["result"] == "5\n"
    assert case["calculation"]["evidence"]["input"] in case["input_messages"][-1]["content"]


async def test_python_trace_does_not_require_provider_function_calling():
    answer_provider = CalculationProvider()
    provider = SimpleNamespace(
        status=answer_provider.status,
        count_input=answer_provider.count_input,
        stream=answer_provider.stream,
    )
    _, case = await evaluate(provider, case_id="code_trace")
    assert case["execution"]["status"] == "completed"
    assert case["calculation"]["status"] == "verified"
    assert case["calculation"]["plan"] is None
    assert case["calculation"]["evidence"]["result"] == "5\n"


@pytest.mark.parametrize(
    "enabled,case_id", [(False, "calculation_discount"), (True, "general_first_step")]
)
async def test_disabled_or_unrelated_requests_do_not_plan_or_change_context(enabled, case_id):
    provider = CalculationProvider()
    _, case = await evaluate(provider, enabled=enabled, case_id=case_id)
    assert provider.plan_calls == []
    assert all("local_calculation" not in message["content"] for message in case["input_messages"])
    assert case["calculation"]["status"] == ("not_needed" if enabled else "disabled")
