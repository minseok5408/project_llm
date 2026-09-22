"""합성 평가에서도 제품과 같은 로컬 계산 계획·검증·참고 자료를 사용한다."""

import asyncio
from contextlib import aclosing

from backend.app.config import Settings
from backend.app.llm.protocol import ChatProvider, ProviderUnavailable, ToolChatProvider
from backend.app.llm.providers.common import _normalized_messages
from backend.app.schemas import ChatMessage
from backend.app.tools.calculation.planning import (
    CALCULATION_TOOLS,
    build_calculation_context,
    direct_python_call,
    plan_messages,
    planning_options,
    resolve_tool,
    should_calculate,
)
from backend.evaluation.scoring import canonical_hash


async def prepare_calculation(
    result: dict,
    messages: list[ChatMessage],
    question: str,
    provider: ChatProvider,
    settings: Settings,
) -> list[ChatMessage]:
    """DB 없이 공통 도구 계약을 실행하고 준비 사용량을 최종 답변과 구분한다."""
    record = result["calculation"] = {
        "status": "not_needed",
        "error": None,
        "plan": None,
        "evidence": None,
    }

    def unverified(reason: str) -> list[ChatMessage]:
        evidence = {"type": "skip", "input": "", "result": "", "verified": False, "reason": reason}
        record["evidence"] = evidence
        return [*messages[:-1], build_calculation_context(evidence), messages[-1]]

    if not should_calculate(question):
        return messages
    try:
        direct = direct_python_call(question)
        if direct is not None:
            evidence = resolve_tool(direct, question)
            record.update(status="verified", evidence=evidence)
            return [*messages[:-1], build_calculation_context(evidence), messages[-1]]
    except ValueError:
        record.update(status="failed", error="invalid_calculation")
        return unverified("unsupported")
    if not isinstance(provider, ToolChatProvider):
        record.update(status="skipped", error="tools_unavailable")
        return unverified("unavailable")

    planning = plan_messages(question)
    options = planning_options()
    normalized = _normalized_messages(planning)
    plan = record["plan"] = {
        "input_messages": normalized,
        "input_sha256": canonical_hash(normalized),
        "tools": CALCULATION_TOOLS,
        "thinking": options.thinking,
        "max_tokens": options.max_tokens,
        "counted_input_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
        "reported_input_tokens": None,
        "reported_output_tokens": None,
        "received_output_tokens": 0,
        "finish_reason": None,
        "usage_status": "missing",
        "tool_calls": [],
    }
    record["status"] = "failed"
    if sum(len(message["content"]) for message in normalized) > settings.llm_max_history_chars:
        record["error"] = "plan_character_limit"
        return unverified("limit")
    final = None
    try:
        counted = await provider.count_tools(planning, options, CALCULATION_TOOLS)
        if type(counted) is not int or counted < 1:
            record["error"] = "invalid_plan_input_count"
            return unverified("unavailable")
        plan["counted_input_tokens"] = counted
        if counted + options.max_tokens > settings.llm_context_window:
            record["error"] = "plan_context_limit"
            return unverified("limit")
        async with (
            asyncio.timeout(min(90, settings.llm_request_timeout_seconds)),
            aclosing(provider.stream_tools(planning, options, CALCULATION_TOOLS)) as stream,
        ):
            async for delta in stream:
                if final is not None:
                    record["error"] = "plan_data_after_final"
                    return unverified("unavailable")
                received = delta.received_output_tokens
                if type(received) is int and 0 <= received <= options.max_tokens:
                    plan["received_output_tokens"] = max(plan["received_output_tokens"], received)
                if delta.final:
                    final = delta
                    plan["finish_reason"] = delta.finish_reason
                    if type(delta.input_tokens) is int and delta.input_tokens >= 0:
                        plan["reported_input_tokens"] = delta.input_tokens
                    if type(delta.output_tokens) is int and delta.output_tokens >= 0:
                        plan["reported_output_tokens"] = delta.output_tokens
        if final is None:
            record["error"] = "missing_plan_usage"
            return unverified("unavailable")
        if (
            type(final.input_tokens) is not int
            or final.input_tokens != counted
            or type(final.output_tokens) is not int
            or not 1 <= final.output_tokens <= options.max_tokens
            or plan["received_output_tokens"] > final.output_tokens
        ):
            plan["usage_status"] = "invalid"
            record["error"] = "invalid_plan_usage"
            return unverified("unavailable")
        plan.update(
            input_tokens=final.input_tokens,
            output_tokens=final.output_tokens,
            usage_status="confirmed",
        )
        plan["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in final.tool_calls
        ]
        if final.finish_reason not in ("stop", "tool_calls") or len(final.tool_calls) != 1:
            record["error"] = "invalid_plan_completion"
            return unverified("unavailable")
        evidence = resolve_tool(final.tool_calls[0], question)
        record["evidence"] = evidence
        record["status"] = "verified" if evidence["verified"] else "skipped"
        context = build_calculation_context(evidence)
        return [*messages[:-1], context, messages[-1]]
    except ProviderUnavailable:
        record["error"] = "plan_provider_unavailable"
        return unverified("unavailable")
    except TimeoutError:
        record["error"] = "plan_timeout"
        return unverified("timeout")
    except ValueError:
        record["error"] = "invalid_calculation"
        return unverified("unsupported")
    finally:
        if final is None and plan["received_output_tokens"]:
            plan["usage_status"] = "received_only"
