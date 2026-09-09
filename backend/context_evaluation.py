"""문맥 구성 계약 검사와 선택적인 실제 모델 어휘 평가를 분리한다."""

import hashlib
import json
import time
import unicodedata
from datetime import UTC, datetime

from backend.app.config import Settings
from backend.app.context.builder import SYSTEM_PROMPT, compose_context
from backend.app.context.compaction import (
    SUMMARY_PROMPT,
    count_context,
    plan_tail,
    summary_batch,
)
from backend.app.context.policy import CONTEXT_POLICY_VERSION, is_short_partial
from backend.app.llm.protocol import ChatProvider, ProviderUnavailable
from backend.app.llm.providers.mock import MockProvider
from backend.app.repositories import InvalidInput
from backend.app.schemas import GenerationOptions
from backend.evaluation_cases import FIXTURE_VERSION, ContextCase


def lexical_score(answer: str, expected: dict[str, tuple[str, ...]]) -> dict:
    """표현 변형을 허용한 어휘 포함 검사이며 사실의 긍정·부정까지 판단하지 않는다."""

    def normalize(value: str) -> str:
        return "".join(unicodedata.normalize("NFKC", value).casefold().split())

    normalized = normalize(answer)
    matched = [
        label
        for label, alternatives in expected.items()
        if any(normalize(term) in normalized for term in alternatives)
    ]
    return {
        "matched": matched,
        "missing": [label for label in expected if label not in matched],
        "matched_count": len(matched),
        "expected_count": len(expected),
        "recall": len(matched) / len(expected) if expected else 1.0,
    }


def check_contract(case: ContextCase) -> dict:
    """요약 전 원문과 역할·종료 안내를 확인하며 모델 답변 성공으로 해석하지 않는다."""
    messages = compose_context(list(case.turns), case.question)
    user_contents = [message.content for message in messages if message.role == "user"]
    assistants = [message.content for message in messages if message.role == "assistant"]
    expected_assistants = [
        turn.assistant_content
        for turn in case.turns
        if turn.assistant_content
        and turn.assistant_content.strip()
        and not is_short_partial(turn.assistant_content, turn.status, turn.finish_reason)
    ]
    partials = [
        {"turn": index, "status": turn.status, "assistant_partial": turn.assistant_content}
        for index, turn in enumerate(case.turns, start=1)
        if is_short_partial(turn.assistant_content, turn.status, turn.finish_reason)
    ]
    checks = {
        "ordered_user_originals": user_contents
        == [*[turn.user_content for turn in case.turns], case.question],
        "assistant_originals": assistants == expected_assistants,
        "latest_request_last": messages[-1].content == case.question,
        "short_partial_originals": not partials
        or json.dumps(partials, ensure_ascii=False, separators=(",", ":")) in messages[0].content,
        "explicit_length_note": not case.turns
        or case.turns[-1].finish_reason != "length"
        or "출력 토큰 한도" in messages[0].content,
    }
    return {"passed": all(checks.values()), "checks": checks}


async def _generate(provider, messages, options) -> tuple[str, dict]:
    """평가에서도 최종 사용량·종료 상태가 없는 생성을 성공으로 처리하지 않는다."""
    input_tokens = await count_context(provider, messages, options)
    chunks = []
    final = None
    async for delta in provider.stream(messages, options):
        chunks.append(delta.text)
        if delta.final:
            final = delta
    if (
        final is None
        or final.input_tokens != input_tokens
        or type(final.output_tokens) is not int
        or not 0 <= final.output_tokens <= options.max_tokens
    ):
        raise ProviderUnavailable("평가 응답의 최종 사용량이 없거나 입력 계산과 다릅니다.")
    return "".join(chunks), {
        "input_tokens": input_tokens,
        "output_tokens": final.output_tokens,
        "finish_reason": final.finish_reason,
    }


async def evaluate_case(
    case: ContextCase,
    settings: Settings,
    *,
    provider: ChatProvider | None = None,
) -> dict:
    """공급자를 생략하면 네트워크 없이 실제 압축 계획 코드의 순서만 검증한다."""
    started = time.monotonic()
    result = {
        "case": case.name,
        "turn_count": len(case.turns),
        "source_output_limit": case.source_output_limit,
        "contract": check_contract(case),
        "model_evaluation": "not_run" if provider is None else "running",
        "lexical": None,
        "summary_usage": [],
        "answer_usage": None,
    }
    counter = provider or MockProvider(settings, delay_seconds=0)
    options = GenerationOptions(thinking=False, max_tokens=128)
    original = list(case.turns)
    tail = []
    summary = None
    summarized_sequences = []
    checkpoints = []
    try:
        # 원문이 추가될 때마다 압축을 판단하여 100턴 이상에서 요약 재사용을 반복한다.
        for index, turn in enumerate(original):
            tail.append(turn)
            next_question = (
                original[index + 1].user_content if index + 1 < len(original) else case.question
            )
            context = compose_context(tail, next_question, summary)
            tokens = await count_context(counter, context, options)
            needs_compaction = (
                tokens + options.max_tokens
                > settings.llm_context_window * settings.llm_compaction_trigger_ratio
                or sum(len(message.content) for message in context) > settings.llm_max_history_chars
            )
            if len(tail) <= 1 or not needs_compaction:
                continue
            keep = await plan_tail(counter, settings, tail, next_question, options, summary)
            pending = tail[:-keep]
            while pending:
                messages, summary_options, _, count = await summary_batch(
                    counter, settings, summary, pending
                )
                selected = pending[:count]
                if provider is None:
                    # 이 표식은 사실 요약이 아니다. 계약 검사에서 재사용·경계만 검증한다.
                    summary = f"계약 검사 전용 요약 표식: {selected[-1].assistant_sequence}"
                else:
                    generated, usage = await _generate(provider, messages, summary_options)
                    result["summary_usage"].append(usage)
                    if not generated.strip() or usage["finish_reason"] != "stop":
                        raise ProviderUnavailable("평가 요약이 완성되지 않았습니다.")
                    summary = generated
                summarized_sequences.extend(item.user_sequence for item in selected)
                checkpoints.append(selected[-1].assistant_sequence)
                pending = pending[count:]
            tail = tail[-keep:]

        result["contract"]["checks"]["rolling_prefix_exactly_once"] = summarized_sequences + [
            turn.user_sequence for turn in tail
        ] == [turn.user_sequence for turn in original]
        result["contract"]["checks"]["originals_unchanged"] = original == list(case.turns)
        result["contract"]["checks"]["checkpoint_increasing"] = all(
            left < right for left, right in zip(checkpoints, checkpoints[1:], strict=False)
        )
        result["contract"]["passed"] = all(result["contract"]["checks"].values())
        result["compaction_count"] = len(checkpoints)
        result["through_sequence"] = checkpoints[-1] if checkpoints else 0
        result["remaining_turns"] = len(tail)
        if provider is not None:
            context = compose_context(tail, case.question, summary)
            tokens = await count_context(provider, context, options)
            if tokens + options.max_tokens > settings.llm_context_window:
                raise ProviderUnavailable("평가의 최종 입력이 모델 문맥 한도를 초과합니다.")
            answer, usage = await _generate(provider, context, options)
            result["answer"] = answer
            result["answer_usage"] = usage
            result["lexical"] = lexical_score(answer, case.expected)
            result["model_evaluation"] = "completed"
    except (ProviderUnavailable, InvalidInput, ValueError) as error:
        result["error"] = str(error)
        result["contract"]["passed"] = False
        if provider is not None:
            result["model_evaluation"] = "failed"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


async def evaluate(
    selected: list[ContextCase],
    settings: Settings,
    *,
    provider: ChatProvider | None = None,
    minimum_recall: float = 1.0,
) -> dict:
    """실제 모델 지표가 없을 때 성공한 코드 계약을 모델 품질 점수로 표시하지 않는다."""
    started = time.monotonic()
    results = [await evaluate_case(case, settings, provider=provider) for case in selected]
    lexical = [item["lexical"] for item in results if item["lexical"] is not None]
    matched = sum(item["matched_count"] for item in lexical)
    expected = sum(item["expected_count"] for item in lexical)
    recall = matched / expected if expected else None
    return {
        "mode": "contract" if provider is None else "model",
        "created_at": datetime.now(UTC).isoformat(),
        "model": None if provider is None else settings.llm_model_id,
        "fixture_version": FIXTURE_VERSION,
        "context_policy_version": CONTEXT_POLICY_VERSION,
        "context_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "summary_prompt_version": 1,
        "summary_prompt_sha256": hashlib.sha256(SUMMARY_PROMPT.encode()).hexdigest(),
        "settings": {
            "context_window": settings.llm_context_window,
            "summary_max_tokens": settings.llm_compaction_max_tokens,
            "trigger_ratio": settings.llm_compaction_trigger_ratio,
            "target_ratio": settings.llm_compaction_target_ratio,
            "keep_turns": settings.llm_compaction_keep_turns,
            "thinking": False,
            "answer_max_tokens": 128,
        },
        "notice": (
            "계약 모드는 원문 보존과 압축 경계만 검사하며 실제 모델의 기억력을 검증하지 않습니다. "
            "모델 모드의 어휘 재현율도 의미·모순·이어쓰기 정확성을 보장하지 않아 "
            "답변 확인이 필요합니다. 평가는 저장 대화나 서비스 예산에 접근하지 않으며 "
            "실제 모델 사용량은 이 보고서에만 기록합니다."
        ),
        "minimum_lexical_recall": minimum_recall,
        "lexical_recall": recall,
        "passed": bool(results)
        and all(item["contract"]["passed"] for item in results)
        and (
            provider is None
            or (
                all(item["model_evaluation"] == "completed" for item in results)
                and recall is not None
                and recall >= minimum_recall
            )
        ),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "cases": results,
    }
