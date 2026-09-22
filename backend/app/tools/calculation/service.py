"""계산 준비를 기존 단계 한도·후정산·중단 규칙 안에서 한 번만 실행한다."""

import asyncio

from sqlalchemy import func, select

from backend.app.context.compaction import count_context
from backend.app.context.dependencies import check_dependencies
from backend.app.llm.protocol import ProviderUnavailable
from backend.app.models import GenerationRun, GenerationStep, Message
from backend.app.repositories import Repository
from backend.app.runtime.cancellation import GenerationCancelled, cancellable
from backend.app.runtime.contracts import GenerationJob, ModelExecution
from backend.app.runtime.steps import StepLimitExceeded, StepService
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.calculation.evaluator import CalculationError
from backend.app.tools.calculation.planning import (
    build_calculation_context,
    direct_python_call,
    plan,
    resolve_tool,
    should_calculate,
)


class CalculationService:
    def __init__(self, execution: ModelExecution):
        self.execution = execution

    async def prepare(self, job: GenerationJob, cancellation: asyncio.Task) -> GenerationJob:
        async with self.execution.database.session() as session:
            run = await session.get(GenerationRun, job["id"])
            question = (await session.get(Message, run.user_message_id)).content
        if not should_calculate(question):
            return job
        result = {
            "type": "skip",
            "input": "",
            "result": "",
            "verified": False,
            "reason": "unavailable",
        }
        ledger = StepService(self.execution.database, self.execution.settings)
        try:
            call = direct_python_call(question)
            async with self.execution.database.session() as session:
                steps = await session.scalar(
                    select(func.count())
                    .select_from(GenerationStep)
                    .where(GenerationStep.generation_id == job["id"])
                )
            # 원문 코드의 직접 추적은 계획을 생략하고 계산·답변 두 자리만 사용한다.
            required_steps = 2 if call is not None else 3
            if steps + required_steps > self.execution.settings.generation_max_steps:
                raise StepLimitExceeded
            if call is None:
                async with asyncio.timeout(
                    min(90, self.execution.settings.llm_request_timeout_seconds)
                ):
                    call = await plan(self.execution, job, question, cancellation)
            if cancellation.done():
                cancellation.result()
                raise GenerationCancelled
            if call.name == "skip_calculation":
                result = resolve_tool(call, question)
            else:
                step = await ledger.start(job, kind="tool", name="calculate", call_id=call.id)
                status, reason = "failed", "calculation_failed"
                try:
                    result = resolve_tool(call, question)
                    if cancellation.done():
                        cancellation.result()
                        raise GenerationCancelled
                    status, reason = "completed", None
                except (GenerationCancelled, asyncio.CancelledError):
                    status, reason = "cancelled", "interrupted"
                    raise
                finally:
                    await asyncio.shield(ledger.close(job, step, status=status, reason=reason))
        except (CalculationError, ProviderUnavailable, StepLimitExceeded, TimeoutError) as error:
            result["reason"] = (
                "unsupported"
                if isinstance(error, CalculationError)
                else "limit"
                if isinstance(error, StepLimitExceeded)
                else "timeout"
                if isinstance(error, TimeoutError)
                else "unavailable"
            )
        return await self._store_context(job, result, cancellation)

    async def _store_context(
        self, job: GenerationJob, result: dict, cancellation: asyncio.Task
    ) -> GenerationJob:
        base = [ChatMessage.model_validate(value) for value in job["messages"]]
        messages = [*base[:-1], build_calculation_context(result), base[-1]]
        options = GenerationOptions.model_validate(job["options"])
        cap = min(
            await StepService(self.execution.database, self.execution.settings).remaining(job),
            self.execution.settings.llm_context_window,
        )
        if (
            sum(len(item.content) for item in messages)
            > self.execution.settings.llm_max_history_chars
        ):
            raise StepLimitExceeded
        tokens = await cancellable(
            count_context(self.execution.provider, messages, options), cancellation
        )
        if tokens < 1 or tokens + 1 > cap:
            raise StepLimitExceeded
        options = options.model_copy(update={"max_tokens": min(options.max_tokens, cap - tokens)})
        async with self.execution.database.session() as session:
            await Repository(session, job["user_id"]).get_conversation(
                job["workspace_id"], job["conversation_id"]
            )
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if (
                run.status != "running"
                or run.cancel_requested
                or not await check_dependencies(session, job["memory_dependencies"])
            ):
                raise GenerationCancelled
            run.prompt_tokens, run.max_output_tokens = tokens, options.max_tokens
            await session.commit()
        # 도구 결과는 현재 실행 메모리에만 둔다. 원문 메시지·도구 인자·SSE에 복제하지 않는다.
        return {
            **job,
            "messages": [message.model_dump() for message in messages],
            "prompt_tokens": tokens,
            "options": {"thinking": options.thinking, "max_tokens": options.max_tokens},
        }
