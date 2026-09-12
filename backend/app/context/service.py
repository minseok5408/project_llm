"""단일 생성 실행자 안에서 대화 요약과 별도의 시스템 사용량을 저장한다."""

import asyncio
from contextlib import aclosing

from sqlalchemy import select, update

from backend.app.context.builder import compose_context, list_context_turns
from backend.app.context.compaction import count_context, plan_tail, summary_batch
from backend.app.context.dependencies import check_dependencies
from backend.app.context.status import context_measurement
from backend.app.llm.protocol import ProviderUnavailable
from backend.app.models import (
    Conversation,
    ConversationCompaction,
    GenerationRun,
    Message,
    TokenReservation,
)
from backend.app.repositories import AccessDenied, InvalidInput, Repository
from backend.app.runtime.cancellation import GenerationCancelled, cancellable
from backend.app.runtime.contracts import GenerationJob, ModelExecution
from backend.app.schemas import GenerationOptions
from backend.app.services.token_quota import QuotaExceeded


async def latest_summary_record(session, conversation: Conversation):
    row = await session.scalar(
        select(ConversationCompaction)
        .where(
            ConversationCompaction.workspace_id == conversation.workspace_id,
            ConversationCompaction.conversation_id == conversation.id,
            ConversationCompaction.model == conversation.model,
            ConversationCompaction.prompt_version == 1,
            ConversationCompaction.status == "completed",
        )
        .order_by(
            ConversationCompaction.through_sequence.desc(), ConversationCompaction.created_at.desc()
        )
        .limit(1)
    )
    if row is not None and await check_dependencies(session, row.memory_dependencies):
        return row
    return None


async def latest_summary(session, conversation: Conversation) -> tuple[str | None, int]:
    row = await latest_summary_record(session, conversation)
    return (row.content, row.through_sequence) if row else (None, 0)


class CompactionService:
    def __init__(self, execution: ModelExecution):
        self.database, self.provider, self.settings = (
            execution.database,
            execution.provider,
            execution.settings,
        )

    async def prepare(self, job: GenerationJob, cancellation: asyncio.Task) -> GenerationJob:
        # 순환 import는 실행 경계에서만 수행해 이벤트 저장 규칙을 한 곳에 유지한다.
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            conversation = await Repository(session, job["user_id"]).get_conversation(
                job["workspace_id"], job["conversation_id"]
            )
            summary, through = await latest_summary(session, conversation)
            turns = await list_context_turns(session, conversation, after_sequence=through)
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if run.cancel_requested:
                raise GenerationCancelled
            content = (await session.get(Message, run.user_message_id)).content
            progress = context_measurement(run, self.settings, through=through, phase="preparing")
            progress["compaction_status"] = "running"
            add_event(
                session,
                run,
                "meta",
                {"status": "running", "stage": "compacting", "context": progress},
            )
            await session.commit()

        options = GenerationOptions.model_validate(job["options"])
        keep = await cancellable(
            plan_tail(self.provider, self.settings, turns, content, options, summary), cancellation
        )
        older, recent = (turns[:-keep], turns[-keep:]) if keep else (turns, [])
        while older:
            messages, summary_options, prompt_tokens, selected = await cancellable(
                summary_batch(self.provider, self.settings, summary, older), cancellation
            )
            summary = await self.summarize(
                job,
                messages,
                summary_options,
                prompt_tokens,
                older[selected - 1].assistant_sequence,
                cancellation,
            )
            through = older[selected - 1].assistant_sequence
            older = older[selected:]

        context = compose_context(recent, content, summary)
        prompt_tokens = await cancellable(
            count_context(self.provider, context, options), cancellation
        )
        if sum(len(message.content) for message in context) > self.settings.llm_max_history_chars:
            raise InvalidInput("요약 후에도 문맥이 너무 깁니다.")
        async with self.database.session() as session:
            await Repository(session, job["user_id"]).get_conversation(
                job["workspace_id"], job["conversation_id"]
            )
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if run.cancel_requested:
                raise GenerationCancelled
            if run.status != "running":
                raise InvalidInput("요약 중 생성 상태가 변경되었습니다.")
            reservation = await session.get(TokenReservation, run.reservation_id)
            remaining_output = (
                min(reservation.reserved_tokens, self.settings.llm_context_window) - prompt_tokens
            )
            if remaining_output < 1:
                raise QuotaExceeded("요약 후 질문과 답변에 사용할 토큰이 부족합니다.")
            options = options.model_copy(
                update={"max_tokens": min(options.max_tokens, remaining_output)}
            )
            run.prompt_tokens, run.max_output_tokens = prompt_tokens, options.max_tokens
            run.thinking = options.thinking
            run.request_messages = [message.model_dump() for message in context]
            run.context_compaction_needed = False
            add_event(
                session,
                run,
                "meta",
                {
                    "status": "running",
                    "stage": "generating",
                    "context_compacted": True,
                    "context": {
                        **context_measurement(
                            run, self.settings, through=through, phase="preparing"
                        ),
                        "compaction_status": "completed",
                    },
                },
            )
            result: GenerationJob = {
                **job,
                "prompt_tokens": prompt_tokens,
                "options": {"thinking": run.thinking, "max_tokens": run.max_output_tokens},
                "messages": list(run.request_messages),
                "context_compaction_needed": False,
            }
            await session.commit()
            return result

    async def summarize(
        self, job: GenerationJob, messages, options, prompt_tokens, through, cancellation
    ) -> str:
        async with self.database.session() as session:
            attempt = ConversationCompaction(
                workspace_id=job["workspace_id"],
                conversation_id=job["conversation_id"],
                generation_id=job["id"],
                model=self.settings.llm_model_id,
                through_sequence=through,
                status="running",
                memory_dependencies=dict(job.get("memory_dependencies", {})),
            )
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id
        content, usage, received, reason = "", None, 0, None
        status, error_code = "failed", "summary_failed"
        try:
            async with aclosing(self.provider.stream(messages, options)) as stream:
                while True:
                    try:
                        delta = await cancellable(anext(stream), cancellation, completed_first=True)
                    except StopAsyncIteration:
                        break
                    count = delta.received_output_tokens
                    if type(count) is int and 0 <= count <= options.max_tokens:
                        received = max(received, count)
                    if delta.final:
                        if (
                            type(delta.input_tokens) is not int
                            or delta.input_tokens != prompt_tokens
                            or type(delta.output_tokens) is not int
                            or not 0 <= delta.output_tokens <= options.max_tokens
                        ):
                            raise ProviderUnavailable("요약 사용량을 확인하지 못했습니다.")
                        usage = (delta.input_tokens, delta.output_tokens, "provider")
                        reason = delta.finish_reason
                    if cancellation.done():
                        cancellation.result()
                        raise GenerationCancelled
                    content += delta.text
                    if len(content) > 100_000:
                        raise ProviderUnavailable("요약 응답 길이가 너무 깁니다.")
            if not content.strip() or usage is None or reason == "length":
                error_code = "summary_incomplete"
                raise ProviderUnavailable("대화 요약이 완성되지 않았습니다.")
            status, error_code = "completed", None
            return content
        except GenerationCancelled:
            status, error_code = "cancelled", "user_cancelled"
            raise
        except asyncio.CancelledError:
            error_code = "worker_stopped"
            raise
        finally:
            if usage is None and received:
                usage = (prompt_tokens, received, "received")
            # 종료 직전의 요약 사용량도 기록하되 사용자 예산에는 어떤 차감도 하지 않는다.
            saved_status = await asyncio.shield(
                self.save_attempt(attempt_id, status, content, usage, error_code)
            )
            if status == "completed" and saved_status != "completed":
                if saved_status == "cancelled":
                    raise GenerationCancelled
                raise AccessDenied("대화 접근 권한 또는 실행 상태가 변경되었습니다.")

    async def save_attempt(self, attempt_id, status, content, usage, error_code):
        async with self.database.session() as session:
            attempt = await session.get(ConversationCompaction, attempt_id)
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == attempt.generation_id)
                .with_for_update()
            )
            if status == "completed":
                if run.cancel_requested or not await check_dependencies(
                    session, attempt.memory_dependencies
                ):
                    status, error_code = "cancelled", "user_cancelled"
                elif run.status != "running":
                    status, error_code = "failed", "run_ended"
                else:
                    try:
                        await Repository(session, run.user_id).get_conversation(
                            run.workspace_id, run.conversation_id
                        )
                    except AccessDenied:
                        status, error_code = "failed", "access_revoked"
            attempt.status = status
            attempt.content = content if status == "completed" else None
            attempt.error_code = error_code
            if usage is not None:
                attempt.input_tokens, attempt.output_tokens, attempt.usage_basis = usage
            await session.commit()
        return status

    async def recover(self):
        async with self.database.session() as session:
            await session.execute(
                update(ConversationCompaction)
                .where(ConversationCompaction.status == "running")
                .values(status="failed", error_code="worker_interrupted")
            )
            await session.commit()
