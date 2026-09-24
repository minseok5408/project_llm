"""DB 없이 실제 검색 정책·로컬 판단 모델·답변의 증거와 사용량을 보존한다."""

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from time import monotonic

from backend.app.config import Settings
from backend.app.llm.protocol import ProviderUnavailable, ToolChatProvider
from backend.app.llm.providers.common import _normalized_messages
from backend.app.llm.providers.mlx import TOOL_SAMPLING_OVERRIDES, MlxServerProvider
from backend.app.schemas import GenerationOptions
from backend.app.tools.web_search.context import (
    SEARCH_CONTEXT_PROMPT,
    build_grounded_search_messages,
    build_search_context,
    build_search_fallback_messages,
    explicit_search_requested,
    prohibited_search_needs_notice,
    should_search,
)
from backend.app.tools.web_search.planning import (
    SEARCH_TOOLS,
    build_planning_messages,
    query_entries,
    validate_call,
)
from backend.app.tools.web_search.provider import (
    SearchProviderError,
    SearchResponse,
    SearchResult,
    WebSearchProvider,
)
from backend.evaluation.runner import build_messages, prepare_report, run_case, sampling_settings
from backend.evaluation.schema import local_model_url
from backend.evaluation.scoring import canonical_hash
from backend.evaluation.search_schema import SearchCase, SearchDataset


def prepare_search_report(
    dataset: SearchDataset,
    dataset_path: Path,
    settings: Settings,
    options: GenerationOptions,
    *,
    model: bool = False,
    live_search: bool = False,
    selected: list[str] | None = None,
    repeat: int = 1,
) -> dict:
    """실행 전 보고서는 정답 기준과 모델 입력을 분리하며 통신하지 않는다."""
    if not settings.web_search_agent_enabled:
        raise ValueError("검색 판단 모델 평가는 web_search_agent_enabled=True 설정이 필요합니다.")
    if live_search and not model:
        raise ValueError("실제 검색은 명시적인 모델 실행에서만 허용합니다.")
    if live_search and (
        not selected
        or any(case.id in selected and not case.live_eligible for case in dataset.cases)
    ):
        raise ValueError("실제 검색은 live_eligible 사례를 --case로 명시해야 합니다.")
    report, _ = prepare_report(
        dataset.answer_dataset(),
        dataset_path,
        settings,
        options,
        model=model,
        selected=selected,
        repeat=repeat,
        local_calculation=False,
    )
    report["search_scope"] = "live_search" if live_search else "synthetic_search"
    report["evaluation_scope"] = {
        "kind": "isolated_search_model",
        "service_integration": False,
        "validated": [
            "검색 후보 규칙·판단 입력·검색어 검증·검색 근거·로컬 모델 답변",
            "현재 모델의 입력 토큰과 출력 상한을 합한 문맥 한도",
        ],
        "limitations": [
            "DB·계정 토큰 예산·서비스 단계 수 한도와 후정산 통합은 검증하지 않습니다.",
            "서비스 _fit의 계정 잔여 예산에 따른 출처 제외·출력 축소는 재현하지 않습니다.",
            "후속 검색 실패도 평가 실행 실패로 기록합니다. "
            "서비스는 확보한 근거로 답변할 수 있습니다.",
            "실서비스 동작은 별도의 DB 통합 테스트와 실제 서비스 실행으로 검증해야 합니다.",
        ],
    }
    report["configuration"]["search"] = {
        "agent_enabled": settings.web_search_agent_enabled,
        "provider": settings.web_search_provider if live_search else "fixture",
        "max_attempts": settings.web_search_max_attempts,
        "max_results": settings.web_search_max_results,
        "planning_max_tokens": settings.web_search_planning_max_tokens,
        "planning_thinking": False,
        "planning_sampling": {**sampling_settings(False), **TOOL_SAMPLING_OVERRIDES},
        "max_query_chars": settings.web_search_max_query_chars,
        "max_context_chars": settings.web_search_max_context_chars,
        "agent_timeout_seconds": settings.web_search_agent_timeout_seconds,
    }
    script = Path(__file__).resolve().parents[2] / "scripts/evaluate_search_quality.py"
    report["implementation"]["search_cli_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
    cases = {case.id: case for case in dataset.cases}
    for result in report["cases"]:
        case = cases[result["case_id"]]
        entries = query_entries(
            {"messages": [message.model_dump() for message in build_messages(case.answer_case())]},
            case.question,
        )
        candidate = should_search(
            case.question,
            "on" if case.search_mode == "on" else "auto",
            recent_queries=[text for key, text in entries.items() if key],
        )
        result["case_definition_sha256"] = canonical_hash(case.model_dump(mode="json"))
        result["search"] = {
            "mode": case.search_mode,
            "network_mode": case.network_mode,
            "expected_search": case.expected_search,
            "policy_gate": candidate,
            "entries": entries,
            "status": "not_run",
            "error": None,
            "planner_steps": [],
            "searches": [],
            "selected_sources": [],
            "elapsed_seconds": None,
            "confirmed_input_tokens": 0,
            "confirmed_output_tokens": 0,
            "usage_complete": True,
        }
    return report


async def _plan(
    trace: dict,
    entries: dict[int, str],
    sources: list[SearchResult],
    queries: list[str],
    provider: ToolChatProvider,
    settings: Settings,
    *,
    mode: str = "auto",
) -> str | None:
    """검증 실패·중단 때도 확인된 사용량과 원래 도구 입력을 보존한다."""
    references = [
        {
            "title": item.title[:300],
            "url": item.url,
            "snippet": item.snippet[:700],
            "retrieved_at": item.retrieved_at.isoformat() if item.retrieved_at else None,
        }
        for item in sources
    ]
    messages = build_planning_messages(entries, references, queries, mode=mode)
    step = {
        "input_messages": _normalized_messages(messages),
        "status": "running",
        "error": None,
        "tool": None,
        "tool_calls": [],
        "query": None,
        "reason": None,
        "counted_input_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
        "received_output_tokens": 0,
        "finish_reason": None,
        "elapsed_seconds": None,
    }
    trace["planner_steps"].append(step)
    options = GenerationOptions(thinking=False, max_tokens=settings.web_search_planning_max_tokens)
    started, final = monotonic(), None
    try:
        if sum(len(message.content) for message in messages) > settings.llm_max_history_chars:
            raise ValueError("planning_character_limit")
        counted = await provider.count_tools(messages, options, SEARCH_TOOLS)
        step["counted_input_tokens"] = counted
        if type(counted) is not int or counted < 1:
            raise ValueError("invalid_input_count")
        if counted + options.max_tokens > settings.llm_context_window:
            raise ValueError("planning_context_limit")
        async with aclosing(provider.stream_tools(messages, options, SEARCH_TOOLS)) as stream:
            async for delta in stream:
                if final is not None:
                    raise ValueError("planning_data_after_final")
                received = delta.received_output_tokens
                if type(received) is int and 0 <= received <= options.max_tokens:
                    step["received_output_tokens"] = max(step["received_output_tokens"], received)
                if delta.final:
                    final = delta
                    step["finish_reason"] = delta.finish_reason
                    step["tool_calls"] = [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in delta.tool_calls
                    ]
                    if (
                        type(delta.input_tokens) is not int
                        or delta.input_tokens != counted
                        or type(delta.output_tokens) is not int
                        or not 0 <= delta.output_tokens <= options.max_tokens
                        or step["received_output_tokens"] > delta.output_tokens
                    ):
                        raise ValueError("planning_invalid_usage")
                    step.update(input_tokens=delta.input_tokens, output_tokens=delta.output_tokens)
        if final is None:
            raise ValueError("planning_missing_final")
        if final.finish_reason == "length" or len(final.tool_calls) != 1:
            raise ValueError("planning_incomplete_call")
        call = final.tool_calls[0]
        query = validate_call(call, entries, settings.web_search_max_query_chars)
        reason = json.loads(call.arguments).get("reason") if query is None else None
        if reason == "sufficient" and not sources:
            raise ValueError("planning_sufficient_without_sources")
        if reason == "not_needed" and explicit_search_requested(entries[0], mode):
            raise ValueError("planning_forced_search_skipped")
        step.update(status="completed", tool=call.name, query=query, reason=reason)
        return query
    except (asyncio.CancelledError, KeyboardInterrupt):
        step.update(status="failed", error="interrupted")
        raise
    except (ValueError, ProviderUnavailable, TimeoutError):
        # 공급자 예외·검증 오류의 원문에는 외부 내용이 들어갈 수 있어 저장하지 않는다.
        step.update(status="failed", error="planning_failed")
        raise
    finally:
        step["elapsed_seconds"] = round(monotonic() - started, 6)
        if step["input_tokens"] is not None and step["output_tokens"] is not None:
            trace["confirmed_input_tokens"] += step["input_tokens"]
            trace["confirmed_output_tokens"] += step["output_tokens"]
        elif step["status"] != "completed":
            trace["usage_complete"] = False


async def _search(
    trace: dict,
    case: SearchCase,
    query: str,
    index: int,
    settings: Settings,
    search_provider: WebSearchProvider | None,
) -> SearchResponse:
    search = {
        "query": query,
        "checked_at": None,
        "sources": [],
        "status": "running",
        "error": None,
        "elapsed_seconds": None,
        "provider": search_provider.name if search_provider else "fixture",
    }
    trace["searches"].append(search)
    started = monotonic()
    try:
        if search_provider is not None:
            response = await search_provider.search(query)
        elif case.fixture_responses:
            fixture = case.fixture_responses[min(index, len(case.fixture_responses) - 1)]
            response = SearchResponse(
                tuple(SearchResult(**item.model_dump()) for item in fixture.sources),
                fixture.checked_at,
            )
        else:
            from datetime import UTC, datetime

            response = SearchResponse((), datetime.now(UTC))
        search["checked_at"] = response.checked_at.isoformat()
        search["sources"] = [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "retrieved_at": (item.retrieved_at or response.checked_at).isoformat(),
            }
            for item in response.results[: settings.web_search_max_results]
        ]
        search["status"] = "completed"
        return response
    except (asyncio.CancelledError, KeyboardInterrupt):
        search.update(status="failed", error="interrupted")
        raise
    except (SearchProviderError, TimeoutError) as error:
        search.update(
            status="failed",
            error=error.code if isinstance(error, SearchProviderError) else "timeout",
        )
        raise
    finally:
        search["elapsed_seconds"] = round(monotonic() - started, 6)


def _diagnostics(result: dict, case: SearchCase) -> None:
    """형식·단어 진단은 의미 품질 통과나 주장 근거 판독으로 간주하지 않는다."""
    trace = result["search"]
    queries = [search["query"] for search in trace["searches"]]
    joined = " ".join(queries).casefold()
    source_ids = {item["id"] for item in trace["selected_sources"]}
    citations = {int(value) for value in re.findall(r"\[(\d+)\]", result["answer"] or "")}
    result["diagnostics"]["search"] = {
        "searched": bool(queries),
        "decision_matches": bool(queries) == case.expected_search,
        "query_missing_terms": [
            term for term in case.query_required if term.casefold() not in joined
        ],
        "query_forbidden_terms": [
            term for term in case.query_forbidden if term.casefold() in joined
        ],
        "invalid_citation_ids": sorted(citations - source_ids),
        "cited_source_ids": sorted(citations & source_ids),
        "claim_support": "requires_human_review",
        "freshness": "requires_human_review",
    }


async def run_search_model(
    report: dict,
    dataset: SearchDataset,
    settings: Settings,
    options: GenerationOptions,
    *,
    provider: ToolChatProvider | None = None,
    search_provider: WebSearchProvider | None = None,
    checkpoint: Callable[[dict], None] | None = None,
) -> dict:
    """합성 사례만 실행하며 실제 공급자는 명시된 실제 검색 보고서에서만 허용한다."""
    local_model_url(settings.llm_base_url)
    if report["mode"] != "model":
        raise ValueError("검색 모델 실행에는 명시적인 모델 보고서가 필요합니다.")
    live = report.get("search_scope") == "live_search"
    if live != (search_provider is not None):
        raise ValueError("실제 검색 실행 허용과 공급자 구성이 일치하지 않습니다.")
    provider = provider or MlxServerProvider(settings)
    definitions = {case.id: case for case in dataset.cases}
    if live and any(not definitions[item["case_id"]].live_eligible for item in report["cases"]):
        raise ValueError("실제 검색에 허용되지 않은 평가 사례가 있습니다.")
    try:
        for result in report["cases"]:
            case, trace = definitions[result["case_id"]], result["search"]
            messages = build_messages(case.answer_case())
            entries = query_entries(
                {"messages": [message.model_dump() for message in messages]}, case.question
            )
            started, sources, queries, error = monotonic(), {}, [], None
            failure_reason = None
            try:
                trace["status"] = "skipped"
                if (
                    trace["policy_gate"]
                    and case.search_mode != "off"
                    and case.network_mode != "local"
                ):
                    async with asyncio.timeout(settings.web_search_agent_timeout_seconds):
                        for index in range(settings.web_search_max_attempts):
                            query = await _plan(
                                trace,
                                entries,
                                list(sources.values()),
                                queries,
                                provider,
                                settings,
                                mode=case.search_mode,
                            )
                            if query is None:
                                trace["status"] = (
                                    "not_needed"
                                    if not queries
                                    and trace["planner_steps"][-1]["reason"] == "not_needed"
                                    else "no_query"
                                    if not queries
                                    else "finished"
                                )
                                break
                            normalized = " ".join(query.split()).casefold()
                            if normalized in queries:
                                trace["status"] = "duplicate_stopped"
                                break
                            queries.append(normalized)
                            response = await _search(
                                trace, case, query, index, settings, search_provider
                            )
                            merged = {
                                item.url: replace(
                                    item, retrieved_at=item.retrieved_at or response.checked_at
                                )
                                for item in response.results
                            }
                            for url, item in sources.items():
                                merged.setdefault(url, item)
                            sources = dict(list(merged.items())[: settings.web_search_max_results])
                            trace["status"] = "searched"
            except (TimeoutError, ProviderUnavailable, ValueError, SearchProviderError) as failure:
                error = "search_preparation_failed"
                failure_reason = (
                    failure.code
                    if isinstance(failure, SearchProviderError)
                    else "timeout"
                    if isinstance(failure, TimeoutError)
                    else "planning_failed"
                )
                trace.update(status="failed", error=error)
            except (asyncio.CancelledError, KeyboardInterrupt):
                trace.update(status="failed", error="interrupted")
                raise
            finally:
                trace["elapsed_seconds"] = round(monotonic() - started, 6)
            reference = build_search_context(
                list(sources.values()), max_chars=settings.web_search_max_context_chars
            )
            if reference is not None:
                messages = build_grounded_search_messages(messages, reference)
                trace["selected_sources"] = json.loads(
                    reference.content[len(SEARCH_CONTEXT_PROMPT) :]
                )["sources"]
            elif (trace["policy_gate"] or prohibited_search_needs_notice(case.question)) and trace[
                "status"
            ] != "not_needed":
                reason = (
                    "search_off"
                    if prohibited_search_needs_notice(case.question)
                    else "forced_local"
                    if case.network_mode == "local"
                    else "search_off"
                    if case.search_mode == "off"
                    else "no_query"
                    if trace["status"] == "no_query"
                    else failure_reason or "no_results"
                )
                trace["fallback_reason"] = reason
                messages = build_search_fallback_messages(messages, reason)
            await run_case(
                result,
                messages,
                case.answer_case(),
                provider,
                settings,
                options,
                local_calculation=False,
            )
            if error:
                result["execution"].update(status="failed", error=error)
            _diagnostics(result, case)
            result["total_elapsed_seconds"] = round(monotonic() - started, 6)
            result["total_confirmed_usage"] = {
                "input_tokens": trace["confirmed_input_tokens"]
                + (result["execution"]["input_tokens"] or 0),
                "output_tokens": trace["confirmed_output_tokens"]
                + (result["execution"]["output_tokens"] or 0),
                "complete": trace["usage_complete"]
                and result["execution"]["usage_status"] == "confirmed",
            }
            report["completed_cases"] = sum(
                item["execution"]["status"] == "completed" for item in report["cases"]
            )
            if checkpoint:
                checkpoint(report)
    finally:
        report["quality_status"] = (
            "pending_review"
            if all(item["execution"]["status"] == "completed" for item in report["cases"])
            else "execution_failed"
        )
        if checkpoint:
            checkpoint(report)
    return report
