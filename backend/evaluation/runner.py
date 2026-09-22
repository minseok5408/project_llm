"""고정 합성 자료로 실제 답변 입력을 만들고 로컬 모델 실행 결과를 기록한다."""

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Callable
from contextlib import aclosing
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic
from uuid import uuid4

from backend.app.config import Settings
from backend.app.context.builder import SYSTEM_PROMPT, ContextTurn, compose_context
from backend.app.context.language import LANGUAGE_REMINDER
from backend.app.files.context import FILE_PROMPT
from backend.app.llm.protocol import ChatProvider, ProviderUnavailable
from backend.app.llm.providers.common import _normalized_messages
from backend.app.llm.providers.mlx import MlxServerProvider
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.web_search.context import SEARCH_CONTEXT_PROMPT, build_search_context
from backend.app.tools.web_search.provider import SearchResult
from backend.context_evaluation import lexical_score
from backend.evaluation.schema import Dataset, QualityCase, local_model_url
from backend.evaluation.scoring import canonical_hash

ROOT = Path(__file__).resolve().parents[2]


def build_messages(case: QualityCase) -> list[ChatMessage]:
    """정답·채점 기준을 제외하고 기존 답변·참고 자료 정책을 재사용한다."""
    turns = [
        ContextTurn(
            index * 2 + 1,
            index * 2 + 2,
            turn.user,
            turn.assistant,
            turn.status,
            turn.finish_reason,
        )
        for index, turn in enumerate(case.turns)
    ]
    messages = compose_context(turns, case.question, case.summary)
    if case.reference_kind == "web":
        reference = build_search_context(
            [
                SearchResult(
                    title="가상 평가 자료",
                    url=f"https://example.org/evaluation/{case.id}",
                    snippet=case.reference_context,
                )
            ]
        )
        if reference is None:
            raise ValueError("평가 참고 자료를 입력에 포함하지 못했습니다.")
        messages.insert(1, reference)
    elif case.reference_kind == "file":
        messages.insert(1, ChatMessage(role="system", content=FILE_PROMPT))
        messages.insert(
            len(messages) - 1,
            ChatMessage(
                role="user",
                content=json.dumps(
                    {
                        "file_excerpts": [
                            {
                                "number": 1,
                                "filename": "평가자료.txt",
                                "page": 1,
                                "content": case.reference_context,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return messages


def implementation_metadata() -> dict:
    """비밀 설정·원격 주소를 읽지 않고 코드·의존성 식별자만 기록한다."""

    def git(*args):
        try:
            return subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    source_files = sorted((ROOT / "backend/app").rglob("*.py"))
    source_files += sorted((ROOT / "backend/evaluation").glob("*.py"))
    source_files.append(ROOT / "scripts/evaluate_answers.py")
    packages = {}
    for name in ("mlx-vlm", "httpx", "pydantic"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": None if status is None else bool(status),
        "code_sha256": canonical_hash(
            {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in source_files
            }
        ),
        "prompt_policy_sha256": canonical_hash(
            [SYSTEM_PROMPT, LANGUAGE_REMINDER, SEARCH_CONTEXT_PROMPT, FILE_PROMPT]
        ),
        "packages": packages,
    }


def sampling_settings(thinking: bool) -> dict:
    """현재 MLX 어댑터의 고정 샘플링 값을 기록하며 시드 재현성을 주장하지 않는다."""
    return {
        "temperature": 1.0 if thinking else 0.7,
        "top_p": 0.95 if thinking else 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0 if thinking else 1.5,
        "repetition_penalty": 1.0,
        "seed": None,
    }


def prepare_report(
    dataset: Dataset,
    dataset_path: Path,
    settings: Settings,
    options: GenerationOptions,
    *,
    model: bool = False,
    selected: list[str] | None = None,
    repeat: int = 1,
) -> tuple[dict, dict[str, list[ChatMessage]]]:
    if type(repeat) is not int or not 1 <= repeat <= 5:
        raise ValueError("반복 횟수는 1부터 5 사이여야 합니다.")
    if selected is not None and (
        not selected
        or len(set(selected)) != len(selected)
        or not set(selected) <= {case.id for case in dataset.cases}
    ):
        raise ValueError("평가 사례 선택이 비어 있거나 중복·미등록 ID를 포함합니다.")
    # 하위 실행 함수로 호출해도 외부 주소로 모델 입력을 보내지 않는다.
    local_model_url(settings.llm_base_url)
    cases = [case for case in dataset.cases if selected is None or case.id in selected]
    prompts = {case.id: build_messages(case) for case in cases}
    report = {
        "schema_version": 1,
        "mode": "model" if model else "contract",
        "scope": "synthetic_answer_only",
        "run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "id": dataset.fixture_id,
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "rubric": dataset.rubric.model_dump(),
            "case_ids": [case.id for case in dataset.cases],
        },
        "implementation": implementation_metadata(),
        "configuration": {
            "model": settings.llm_model_id if model else None,
            "context_window": settings.llm_context_window,
            "max_tokens": options.max_tokens,
            "thinking": options.thinking,
            "timeout_seconds": settings.llm_request_timeout_seconds,
            "max_input_chars": settings.llm_max_history_chars,
            "repeat": repeat,
            "sampling": sampling_settings(options.thinking),
        },
        "measurements": {"peak_memory_bytes": None, "memory_status": "not_measured"},
        "quality_status": "pending_review" if model else "not_run",
        "completed_cases": 0,
        "total_cases": len(cases) * repeat,
        "cases": [],
    }
    for case in cases:
        for repetition in range(1, repeat + 1):
            report["cases"].append(
                {
                    "case_id": case.id,
                    "repeat": repetition,
                    "category": case.category,
                    "question": case.question,
                    "expected_language": case.expected_language,
                    "criteria": [item.model_dump() for item in case.criteria],
                    "case_definition_sha256": canonical_hash(case.model_dump()),
                    "input_messages": _normalized_messages(prompts[case.id]),
                    "input_sha256": canonical_hash(_normalized_messages(prompts[case.id])),
                    "answer": None,
                    "answer_sha256": None,
                    "execution": {
                        "status": "not_run",
                        "error": None,
                        "counted_input_tokens": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "reported_input_tokens": None,
                        "reported_output_tokens": None,
                        "usage_status": "missing",
                        "received_output_tokens": None,
                        "finish_reason": None,
                        "elapsed_seconds": None,
                        "first_token_seconds": None,
                    },
                    "diagnostics": {"lexical": []},
                }
            )
    return report, prompts


async def run_case(
    result: dict,
    messages: list[ChatMessage],
    case: QualityCase,
    provider: ChatProvider,
    settings: Settings,
    options: GenerationOptions,
) -> None:
    """실패한 실행도 답변·확인된 사용량을 보존하며 재시도하지 않는다."""
    execution = result["execution"]
    started = monotonic()
    result["answer"] = ""
    final = None
    error = None
    try:
        async with asyncio.timeout(settings.llm_request_timeout_seconds):
            if sum(len(m["content"]) for m in _normalized_messages(messages)) > (
                settings.llm_max_history_chars
            ):
                error = "input_character_limit"
            else:
                counted = await provider.count_input(messages, options)
                if type(counted) is not int or counted < 1:
                    error = "invalid_input_count"
                else:
                    execution["counted_input_tokens"] = counted
                    if counted + options.max_tokens > settings.llm_context_window:
                        error = "context_limit"
            if error is None:
                async with aclosing(provider.stream(messages, options)) as stream:
                    async for delta in stream:
                        if final is not None:
                            error = "data_after_final"
                            break
                        if delta.text:
                            if execution["first_token_seconds"] is None:
                                execution["first_token_seconds"] = round(monotonic() - started, 6)
                            result["answer"] += delta.text
                            if len(result["answer"]) > 200_000:
                                error = "answer_character_limit"
                                break
                        received = delta.received_output_tokens
                        if type(received) is int and 0 <= received <= options.max_tokens:
                            execution["received_output_tokens"] = max(
                                execution["received_output_tokens"] or 0, received
                            )
                        if delta.final:
                            final = delta
                            execution["finish_reason"] = delta.finish_reason
                            # 무효 사용량은 확정값이나 0으로 바꾸어 기록하지 않는다.
                            if type(delta.input_tokens) is int and delta.input_tokens >= 0:
                                execution["reported_input_tokens"] = delta.input_tokens
                            if type(delta.output_tokens) is int and delta.output_tokens >= 0:
                                execution["reported_output_tokens"] = delta.output_tokens
                            if (
                                type(delta.input_tokens) is not int
                                or delta.input_tokens != execution["counted_input_tokens"]
                                or type(delta.output_tokens) is not int
                                or not 0 <= delta.output_tokens <= options.max_tokens
                                or (result["answer"].strip() and delta.output_tokens == 0)
                                or (
                                    execution["received_output_tokens"] is not None
                                    and execution["received_output_tokens"] > delta.output_tokens
                                )
                            ):
                                error = "invalid_final_usage"
                                execution["usage_status"] = "invalid"
                            else:
                                execution["input_tokens"] = delta.input_tokens
                                execution["output_tokens"] = delta.output_tokens
                                execution["usage_status"] = "confirmed"
                                if delta.finish_reason != "stop":
                                    error = "incomplete_answer"
                                elif delta.tool_calls:
                                    error = "unexpected_tool_call"
    except TimeoutError:
        error = "timeout"
    except ProviderUnavailable:
        error = "provider_unavailable"
    except (asyncio.CancelledError, KeyboardInterrupt):
        error = "interrupted"
        raise
    finally:
        if error is None:
            if final is None:
                error = "missing_final_usage"
            elif not result["answer"].strip():
                error = "empty_answer"
        execution.update(
            status="failed" if error else "completed",
            error=error,
            elapsed_seconds=round(monotonic() - started, 6),
        )
        result["answer_sha256"] = canonical_hash(result["answer"])
        if final is None and execution["received_output_tokens"] is not None:
            execution["usage_status"] = "received_only"
        result["diagnostics"]["lexical"] = [
            {
                "id": check.id,
                "kind": check.kind,
                "matched": bool(
                    lexical_score(result["answer"], {check.id: tuple(check.terms)})["matched_count"]
                ),
            }
            for check in case.lexical_checks
        ]


async def run_model(
    report: dict,
    prompts: dict[str, list[ChatMessage]],
    dataset: Dataset,
    settings: Settings,
    options: GenerationOptions,
    *,
    checkpoint: Callable[[dict], None] | None = None,
    provider: ChatProvider | None = None,
) -> dict:
    local_model_url(settings.llm_base_url)
    if report["mode"] != "model":
        raise ValueError("모델 실행에는 명시적인 모델 보고서가 필요합니다.")
    provider = provider or MlxServerProvider(settings)
    definitions = {case.id: case for case in dataset.cases}
    try:
        for result in report["cases"]:
            await run_case(
                result,
                prompts[result["case_id"]],
                definitions[result["case_id"]],
                provider,
                settings,
                options,
            )
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
