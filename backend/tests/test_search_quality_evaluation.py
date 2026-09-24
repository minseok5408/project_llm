"""검색 평가의 무통신 기본값·증거 보존·사용량·실패와 사람 채점 연결을 검증한다."""

import asyncio
import copy
import json
import socket
from datetime import UTC, datetime

import pytest

from backend.app.llm.protocol import ProviderDelta, ToolCall
from backend.app.schemas import GenerationOptions
from backend.app.tools.web_search.provider import SearchProviderError, SearchResponse, SearchResult
from backend.evaluation import runner, search_runner
from backend.evaluation.scoring import assess, review_template
from backend.evaluation.search_schema import DEFAULT_SEARCH_DATASET, load_search_dataset
from scripts import evaluate_answers
from scripts import evaluate_search_quality as cli


class ScriptedProvider:
    """검색 판단·답변을 재현하며 실제 모델이나 외부 검색을 호출하지 않는다."""

    def __init__(self, calls, *, input_tokens=40, output_tokens=10, interruption=False):
        self.calls = iter(calls)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.interruption = interruption
        self.planning_calls = 0
        self.closed = False

    async def count_tools(self, messages, options, tools):
        return 40

    async def stream_tools(self, messages, options, tools):
        self.planning_calls += 1
        try:
            yield ProviderDelta(received_output_tokens=4)
            if self.interruption:
                raise asyncio.CancelledError
            yield ProviderDelta(
                final=True,
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                finish_reason="tool_calls",
                tool_calls=(next(self.calls),),
            )
        finally:
            self.closed = True

    async def count_input(self, messages, options):
        return 80

    async def stream(self, messages, options):
        yield ProviderDelta(text="제공한 자료의 설명입니다. [1]", received_output_tokens=5)
        yield ProviderDelta(final=True, input_tokens=80, output_tokens=8, finish_reason="stop")


class SearchProvider:
    name = "test"
    configured = True

    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def search(self, query):
        self.calls.append(query)
        if self.fail:
            raise SearchProviderError("rate_limited")
        return SearchResponse(
            (SearchResult("공식 기록", "https://example.org/release", "버전 9.0"),),
            datetime(2026, 9, 20, tzinfo=UTC),
        )


def search_call(*terms):
    return ToolCall(
        "case-call",
        "web_search",
        json.dumps(
            {"terms": [{"message": message, "text": text} for message, text in terms]},
            ensure_ascii=False,
        ),
    )


def finish_call(reason="sufficient"):
    return ToolCall("finish-call", "finish_search", json.dumps({"reason": reason}))


@pytest.fixture(autouse=True)
def isolated_metadata(monkeypatch):
    monkeypatch.setattr(runner, "implementation_metadata", lambda: {"commit": "test"})


def prepared(case="latest_python", *, live=False):
    dataset = load_search_dataset()
    settings = evaluate_answers.configured(cli.parser().parse_args(["validate"]))
    options = GenerationOptions(thinking=False, max_tokens=128)
    report = search_runner.prepare_search_report(
        dataset,
        DEFAULT_SEARCH_DATASET,
        settings,
        options,
        model=True,
        live_search=live,
        selected=[case],
    )
    return report, dataset, settings, options


def test_default_validation_never_constructs_provider_or_connects(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("무통신 평가 검증에서 모델·검색·네트워크를 호출했습니다.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(cli, "build_search_provider", forbidden)
    monkeypatch.setattr(cli, "run_search_model", forbidden)
    assert cli.main([]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["cases"] == 23
    assert summary["search_scope"] == "synthetic_search"
    assert summary["quality_status"] == "not_run"


def test_fixture_covers_required_categories_and_excludes_answers_from_inputs():
    dataset = load_search_dataset()
    assert {case.category for case in dataset.cases} >= {
        "no_search_transform",
        "no_search_code",
        "no_search_definition",
        "no_search_general",
        "no_search_calculation",
        "no_search_memory",
        "no_search_policy",
        "explicit_search",
        "current_release",
        "context_query",
        "context_privacy",
        "context_missing",
        "current_fact",
        "recommendation",
        "freshness",
        "conflict",
        "source_injection",
        "empty_results",
    }
    for case in dataset.cases:
        before = search_runner.build_messages(case.answer_case())
        altered = case.model_copy(deep=True)
        altered.query_required = ["PRIVATE_EXPECTED_QUERY"]
        altered.expected_search = not case.expected_search
        altered.criteria[0].description = "PRIVATE_EVAL_CRITERION"
        assert search_runner.build_messages(altered.answer_case()) == before


@pytest.mark.parametrize("case", [None, "injected_evidence", "privacy_context"])
def test_live_requires_explicit_public_fixture_selection(case):
    dataset = load_search_dataset()
    settings = evaluate_answers.configured(cli.parser().parse_args(["validate"]))
    with pytest.raises(ValueError, match="live_eligible"):
        search_runner.prepare_search_report(
            dataset,
            DEFAULT_SEARCH_DATASET,
            settings,
            GenerationOptions(),
            model=True,
            live_search=True,
            selected=[case] if case else None,
        )


def test_disabled_search_agent_is_rejected_before_model_execution():
    _, dataset, settings, options = prepared()
    settings.web_search_agent_enabled = False
    with pytest.raises(ValueError, match="web_search_agent_enabled=True"):
        search_runner.prepare_search_report(dataset, DEFAULT_SEARCH_DATASET, settings, options)


def test_report_declares_isolated_scope_and_uncovered_service_budgets():
    value = prepared()[0]
    assert value["configuration"]["search"]["agent_enabled"] is True
    scope = value["evaluation_scope"]
    assert scope["kind"] == "isolated_search_model"
    assert scope["service_integration"] is False
    assert "_fit" in " ".join(scope["limitations"])
    assert "문맥 한도" in " ".join(scope["validated"])


@pytest.mark.parametrize("thinking", [False, True])
def test_planning_sampling_is_separate_from_answer_sampling(thinking):
    _, dataset, settings, _ = prepared()
    report = search_runner.prepare_search_report(
        dataset, DEFAULT_SEARCH_DATASET, settings, GenerationOptions(thinking=thinking)
    )
    answer = report["configuration"]["sampling"]
    planning = report["configuration"]["search"]
    assert answer["temperature"] == (1.0 if thinking else 0.7)
    assert answer["presence_penalty"] == (0.0 if thinking else 1.5)
    assert planning["planning_thinking"] is False
    assert planning["planning_sampling"] == {
        **runner.sampling_settings(False),
        **search_runner.TOOL_SAMPLING_OVERRIDES,
    }
    assert planning["planning_sampling"]["temperature"] == 0.0
    assert planning["planning_sampling"]["presence_penalty"] == 0.0
    assert planning["planning_sampling"]["seed"] is None
    assert planning["planning_sampling"] is not answer


async def test_synthetic_run_preserves_queries_sources_usage_and_review_binding():
    arguments = prepared()
    provider = ScriptedProvider([search_call((0, "Python"), (0, "최신 안정판")), finish_call()])
    report = await search_runner.run_search_model(*arguments, provider=provider)
    result = report["cases"][0]
    trace = result["search"]
    assert report["quality_status"] == "pending_review"
    assert trace["searches"][0]["query"] == "Python 최신 안정판"
    assert trace["searches"][0]["checked_at"] == "2026-09-20T03:00:00+00:00"
    assert trace["selected_sources"][0]["retrieved_at"] == "2026-09-20T03:00:00+00:00"
    assert trace["confirmed_input_tokens"] == 80
    assert trace["confirmed_output_tokens"] == 20
    assert trace["planner_steps"][0]["tool_calls"][0]["id"] == "case-call"
    assert json.loads(trace["planner_steps"][0]["tool_calls"][0]["arguments"])["terms"] == [
        {"message": 0, "text": "Python"},
        {"message": 0, "text": "최신 안정판"},
    ]
    assert trace["elapsed_seconds"] >= 0
    assert result["total_confirmed_usage"] == {
        "input_tokens": 160,
        "output_tokens": 28,
        "complete": True,
    }
    assert result["diagnostics"]["search"]["decision_matches"] is True
    assert result["diagnostics"]["search"]["claim_support"] == "requires_human_review"
    template = review_template(report)
    assert assess(report, template)["status"] == "pending_review"
    altered = copy.deepcopy(report)
    altered["cases"][0]["search"]["searches"][0]["sources"][0]["snippet"] = "바뀐 근거"
    with pytest.raises(ValueError, match="보고서와 일치"):
        assess(altered, template)


async def test_grounded_answer_reuses_service_copy_and_preserves_query_inputs():
    arguments = prepared("public_followup")
    definition = next(case for case in arguments[1].cases if case.id == "public_followup")
    original = search_runner.build_messages(definition.answer_case())
    original_copy = copy.deepcopy(original)
    original_entries = copy.deepcopy(arguments[0]["cases"][0]["search"]["entries"])
    report = await search_runner.run_search_model(
        *arguments,
        provider=ScriptedProvider(
            [search_call((1, "삼성전자"), (0, "최신 분기 실적")), finish_call()]
        ),
    )
    result = report["cases"][0]
    fixture = definition.fixture_responses[0]
    reference = search_runner.build_search_context(
        [
            SearchResult(source.title, source.url, source.snippet, fixture.checked_at)
            for source in fixture.sources
        ],
        max_chars=arguments[2].web_search_max_context_chars,
    )
    assert reference is not None
    expected = search_runner.build_grounded_search_messages(original, reference)
    assert result["input_messages"] == search_runner._normalized_messages(expected)
    assert original == original_copy
    assert result["question"] == definition.question
    assert result["search"]["entries"] == original_entries
    assert result["search"]["searches"][0]["query"] == "삼성전자 최신 분기 실적"
    assert all("Search evidence policy" not in text for text in original_entries.values())
    assert "Search evidence policy" not in json.dumps(result["search"]["planner_steps"])
    final_user = next(message.content for message in reversed(expected) if message.role == "user")
    assert final_user.startswith(definition.question + "\n\n[Search evidence policy for this turn]")
    assert "untrusted reference data" in final_user
    assert "Never infer latest from the first list entry" in final_user


@pytest.mark.parametrize(
    "case", ["translate_today", "search_off", "network_local", "explicit_no_search"]
)
async def test_search_disabled_or_unneeded_has_zero_external_calls(case):
    arguments = prepared(case, live=True)
    provider, search = ScriptedProvider([]), SearchProvider()
    report = await search_runner.run_search_model(
        *arguments, provider=provider, search_provider=search
    )
    assert search.calls == []
    assert provider.planning_calls == 0
    assert report["cases"][0]["diagnostics"]["search"]["decision_matches"] is True


async def test_semantic_not_needed_preserves_planning_usage_without_search():
    arguments = prepared("purchase_recommendation", live=True)
    provider, search = ScriptedProvider([finish_call("not_needed")]), SearchProvider()
    report = await search_runner.run_search_model(
        *arguments, provider=provider, search_provider=search
    )
    result = report["cases"][0]
    assert search.calls == []
    assert result["search"]["status"] == "not_needed"
    assert (
        '"explicit_search_requested": false'
        in result["search"]["planner_steps"][0]["input_messages"][-1]["content"]
    )
    assert result["total_confirmed_usage"]["input_tokens"] == 120
    assert result["diagnostics"]["search"]["decision_matches"] is False


@pytest.mark.parametrize(
    ("case", "reason", "calls"),
    [
        ("ambiguous_subject", "no_query", [finish_call("no_query")]),
        ("network_local", "forced_local", []),
        ("search_off", "search_off", []),
        ("explicit_no_search", "search_off", []),
        ("empty_results", "no_results", [search_call((0, "TestDelta")), finish_call("no_query")]),
        ("latest_python", "planning_failed", [finish_call("sufficient")]),
    ],
)
async def test_fallback_reuses_service_copy_and_preserves_original_question(case, reason, calls):
    arguments = prepared(case)
    definition = next(item for item in arguments[1].cases if item.id == case)
    original_question = definition.question
    original_entries = copy.deepcopy(arguments[0]["cases"][0]["search"]["entries"])
    report = await search_runner.run_search_model(*arguments, provider=ScriptedProvider(calls))
    result = report["cases"][0]
    expected = search_runner.build_search_fallback_messages(
        search_runner.build_messages(definition.answer_case()), reason
    )
    assert result["input_messages"] == search_runner._normalized_messages(expected)
    assert result["search"]["fallback_reason"] == reason
    last_user = next(
        message["content"]
        for message in reversed(result["input_messages"])
        if message["role"] == "user"
    )
    metadata = last_user.split("[Server-verified search status for this turn]\n", 1)[1]
    state = json.loads(metadata.split("\n", 1)[0])
    assert state == {
        "reason": reason,
        "evidence_available": False,
        "answer_mode": "ask_subject" if reason == "no_query" else "self_contained_or_notice",
    }
    assert definition.question == result["question"] == original_question
    assert result["search"]["entries"] == original_entries
    assert all("Server-verified search status" not in text for text in original_entries.values())
    assert last_user.startswith(original_question)
    assert "Choose the response language only from the original user request above" in last_user


async def test_self_contained_fixture_preserves_local_and_off_response_paths():
    path = DEFAULT_SEARCH_DATASET.parents[3] / "data/evaluations/search-self-contained-v1.json"
    dataset = load_search_dataset(path)
    assert dataset.fixture_id == "search-self-contained-v1"
    assert len(dataset.cases) == 4
    assert all(not case.expected_search and not case.live_eligible for case in dataset.cases)
    assert all(not case.fixture_responses for case in dataset.cases)
    _, _, settings, options = prepared()
    report = search_runner.prepare_search_report(dataset, path, settings, options, model=True)
    provider = ScriptedProvider([])
    report = await search_runner.run_search_model(
        report, dataset, settings, options, provider=provider
    )
    assert provider.planning_calls == 0
    assert report["search_scope"] == "synthetic_search"
    expected_reasons = {
        "arithmetic_today_local": "forced_local",
        "translate_today_local": "forced_local",
        "stable_knowledge_off": None,
        "everyday_today_off": "search_off",
    }
    for case, result in zip(dataset.cases, report["cases"], strict=True):
        reason = expected_reasons[case.id]
        assert result["search"].get("fallback_reason") == reason
        assert result["search"]["searches"] == []
        assert result["diagnostics"]["search"]["decision_matches"] is True
        original = search_runner.build_messages(case.answer_case())
        expected = (
            search_runner.build_search_fallback_messages(original, reason) if reason else original
        )
        assert result["input_messages"] == search_runner._normalized_messages(expected)
        assert result["question"] == case.question
        assert "Server-verified search status" not in case.question
        if reason:
            last_user = next(
                message.content for message in reversed(expected) if message.role == "user"
            )
            assert '"answer_mode":"self_contained_or_notice"' in last_user
            assert "external search evidence" in last_user
    assert len(load_search_dataset().cases) == 23


async def test_context_query_uses_subject_from_previous_user_message():
    arguments = prepared("public_followup")
    provider = ScriptedProvider(
        [search_call((1, "삼성전자"), (0, "최신 분기 실적")), finish_call()]
    )
    report = await search_runner.run_search_model(*arguments, provider=provider)
    result = report["cases"][0]
    assert result["search"]["searches"][0]["query"] == "삼성전자 최신 분기 실적"
    assert result["diagnostics"]["search"]["query_missing_terms"] == []


async def test_repeated_query_does_not_call_search_twice():
    arguments = prepared(live=True)
    call = search_call((0, "Python"))
    search = SearchProvider()
    report = await search_runner.run_search_model(
        *arguments,
        provider=ScriptedProvider([call, call]),
        search_provider=search,
    )
    assert search.calls == ["Python"]
    assert report["cases"][0]["search"]["status"] == "duplicate_stopped"


async def test_invalid_usage_blocks_external_search_and_cannot_pass_review():
    arguments = prepared(live=True)
    provider = ScriptedProvider([search_call((0, "Python"))], input_tokens=41)
    search = SearchProvider()
    report = await search_runner.run_search_model(
        *arguments, provider=provider, search_provider=search
    )
    trace = report["cases"][0]["search"]
    assert search.calls == []
    assert trace["confirmed_input_tokens"] == 0
    assert trace["usage_complete"] is False
    assert trace["planner_steps"][0]["received_output_tokens"] == 4
    assert report["quality_status"] == "execution_failed"
    assert assess(report, review_template(report))["passed"] is False


async def test_sufficient_without_sources_is_a_failure():
    report = await search_runner.run_search_model(
        *prepared(),
        provider=ScriptedProvider([finish_call()]),
    )
    assert report["quality_status"] == "execution_failed"
    assert report["cases"][0]["search"]["confirmed_input_tokens"] == 40


@pytest.mark.parametrize("mode", ["on", "auto"])
async def test_explicit_search_rejects_not_needed_and_preserves_invalid_call(mode):
    report, dataset, settings, options = prepared()
    case = next(case for case in dataset.cases if case.id == "latest_python")
    case.search_mode = mode
    report = await search_runner.run_search_model(
        report,
        dataset,
        settings,
        options,
        provider=ScriptedProvider([finish_call("not_needed")]),
    )
    assert report["quality_status"] == "execution_failed"
    step = report["cases"][0]["search"]["planner_steps"][0]
    assert '"explicit_search_requested": true' in step["input_messages"][-1]["content"]
    assert json.loads(step["tool_calls"][0]["arguments"])["reason"] == "not_needed"
    assert step["input_tokens"] == 40


async def test_live_failure_is_sanitized_and_preserves_query_and_planning_usage():
    report = await search_runner.run_search_model(
        *prepared(live=True),
        provider=ScriptedProvider([search_call((0, "Python"))]),
        search_provider=SearchProvider(fail=True),
    )
    result = report["cases"][0]
    assert result["search"]["searches"][0]["error"] == "rate_limited"
    assert result["search"]["fallback_reason"] == "rate_limited"
    assert result["search"]["searches"][0]["query"] == "Python"
    assert result["total_confirmed_usage"]["input_tokens"] == 120
    assert report["quality_status"] == "execution_failed"


async def test_interruption_closes_stream_and_checkpoints_received_usage():
    arguments = prepared()
    checkpoints = []
    provider = ScriptedProvider([], interruption=True)
    with pytest.raises(asyncio.CancelledError):
        await search_runner.run_search_model(
            *arguments,
            provider=provider,
            checkpoint=lambda report: checkpoints.append(copy.deepcopy(report)),
        )
    trace = checkpoints[-1]["cases"][0]["search"]
    assert provider.closed is True
    assert trace["planner_steps"][0]["received_output_tokens"] == 4
    assert trace["usage_complete"] is False
    assert trace["error"] == "interrupted"
    assert checkpoints[-1]["quality_status"] == "execution_failed"


async def test_synthetic_mode_rejects_live_provider():
    with pytest.raises(ValueError, match="허용과 공급자"):
        await search_runner.run_search_model(*prepared(), search_provider=SearchProvider())
