"""서버 대화 원문, 종료 후 토큰 차감, 영속 생성 작업과 단일 실행자를 연결한다."""

import asyncio
import hashlib
import json
import logging
from contextlib import aclosing
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models import (
    Conversation,
    GenerationEvent,
    GenerationRun,
    Message,
    TokenReservation,
    User,
)
from backend.app.providers import ChatProvider, ProviderUnavailable
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, Repository
from backend.app.repositories.core import logged, required_text
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.compaction_service import (
    CompactionCancelled,
    CompactionService,
    latest_summary,
)
from backend.app.services.context_compaction import count_context
from backend.app.services.conversation_context import (
    SYSTEM_PROMPT as SYSTEM_PROMPT,
)
from backend.app.services.conversation_context import (
    compose_context,
    list_context_turns,
)
from backend.app.services.monthly_allowance import MonthlyAllowanceService
from backend.app.services.token_quota import QuotaExceeded, TokenQuotaService

logger = logging.getLogger(__name__)
ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "usage_pending")
ADMISSION_LOCK = 7160524630128422
WORKER_LOCK = 7160524630128423


class QueueFull(Conflict):
    """현재 계정 또는 전체 생성 대기열에 여유가 없다."""


def now() -> datetime:
    return datetime.now(UTC)


def run_payload(run: GenerationRun) -> dict:
    return {
        "id": str(run.id),
        "generation_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "user_message_id": str(run.user_message_id),
        "assistant_message_id": str(run.assistant_message_id),
        "status": run.status,
        "cancel_requested": run.cancel_requested,
        "last_event_id": run.last_event_sequence,
        "error_code": run.error_code,
        "events_url": f"/api/v1/generations/{run.id}/events",
        "created_at": run.created_at.isoformat(),
    }


def add_event(session: AsyncSession, run: GenerationRun, kind: str, payload: dict) -> None:
    run.last_event_sequence += 1
    session.add(
        GenerationEvent(
            generation_id=run.id,
            sequence=run.last_event_sequence,
            kind=kind,
            payload={"generation_id": str(run.id), **payload},
        )
    )


async def accessible_run(session: AsyncSession, actor_id: UUID, run_id: UUID) -> GenerationRun:
    run = await session.get(GenerationRun, run_id)
    if run is None:
        raise AccessDenied("대상에 접근할 수 없습니다.")
    await Repository(session, actor_id).get_conversation(run.workspace_id, run.conversation_id)
    return run


async def context_messages(
    session: AsyncSession, conversation: Conversation, content: str
) -> list[ChatMessage]:
    summary, through = await latest_summary(session, conversation)
    turns = await list_context_turns(session, conversation, after_sequence=through)
    return compose_context(turns, content, summary)


class GenerationService:
    def __init__(self, database: Database, provider: ChatProvider, settings: Settings):
        self.database, self.provider, self.settings = database, provider, settings
        self.cancel_events: dict[UUID, asyncio.Event] = {}

    @logged
    async def submit(
        self,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        content: str,
        options: GenerationOptions,
        idempotency_key: UUID,
    ) -> dict:
        content = required_text(content, 100_000, "메시지")
        await self.recover_unsettled(actor_id)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "conversation_id": str(conversation_id),
                    "content": content,
                    "options": options.model_dump(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        async with self.database.session() as session:
            conversation = await Repository(session, actor_id).get_conversation_by_id(
                conversation_id
            )
            existing = await session.scalar(
                select(GenerationRun).where(
                    GenerationRun.user_id == actor_id,
                    GenerationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise Conflict("같은 요청 키에 다른 내용을 보낼 수 없습니다.")
                return run_payload(existing)
            if conversation.status != "active":
                raise Conflict("보관한 대화에는 메시지를 보낼 수 없습니다.")
            if conversation.model != self.settings.llm_model_id:
                raise Conflict("이 대화의 모델과 현재 모델이 다릅니다. 새 대화를 시작하세요.")
            if await session.scalar(
                select(GenerationRun.id)
                .where(
                    GenerationRun.status.in_(ACTIVE_STATUSES),
                    (GenerationRun.user_id == actor_id)
                    | (GenerationRun.conversation_id == conversation_id),
                )
                .limit(1)
            ):
                raise QueueFull("이미 진행 중인 응답이 있습니다. 완료 후 다시 보내세요.")
            summary, through = await latest_summary(session, conversation)
            turns = await list_context_turns(session, conversation, after_sequence=through)
            context = compose_context(turns, content, summary)
            version = conversation.next_message_sequence
        too_many_chars = (
            sum(len(message.content) for message in context) > self.settings.llm_max_history_chars
        )
        prompt_tokens = (
            self.settings.llm_context_window
            if too_many_chars
            else await count_context(self.provider, context, options)
        )
        compaction_needed = len(turns) > 1 and (
            too_many_chars
            or prompt_tokens + options.max_tokens
            >= self.settings.llm_context_window * self.settings.llm_compaction_trigger_ratio
        )
        if compaction_needed:
            # 압축 전 원문 대신 반드시 보존할 최근 답변·현재 질문으로 최소 실행 가능성을 확인한다.
            # 최종 입력량과 출력 상한은 단일 실행자가 요약을 마친 뒤 다시 확정한다.
            context = compose_context(turns[-1:], content)
            prompt_tokens = await count_context(self.provider, context, options)
        if (
            sum(len(message.content) for message in context) > self.settings.llm_max_history_chars
            or prompt_tokens + options.max_tokens > self.settings.llm_context_window
        ):
            raise InvalidInput(
                "최근 답변과 질문이 모델 문맥 한도를 초과합니다. 질문이나 출력 길이를 줄여 주세요."
            )
        async with self.database.session() as session:
            # 대기열 승인만 직렬화하고 tokenizer·추론 중에는 잠금이나 트랜잭션을 유지하지 않는다.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK}
            )
            await session.scalar(select(User).where(User.id == actor_id).with_for_update())
            repository = Repository(session, actor_id)
            conversation = await repository.get_conversation_by_id(conversation_id)
            await repository._require_membership(conversation.workspace_id, lock=True)
            conversation = await session.scalar(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            existing = await session.scalar(
                select(GenerationRun).where(
                    GenerationRun.user_id == actor_id,
                    GenerationRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise Conflict("같은 요청 키에 다른 내용을 보낼 수 없습니다.")
                return run_payload(existing)
            if conversation.status != "active" or conversation.deleted_at is not None:
                raise Conflict("이 대화에는 메시지를 보낼 수 없습니다.")
            if conversation.next_message_sequence != version:
                raise Conflict("대화가 변경되었습니다. 내용을 새로 불러온 뒤 다시 보내세요.")
            active = select(GenerationRun).where(GenerationRun.status.in_(ACTIVE_STATUSES))
            if await session.scalar(
                select(func.count()).select_from(
                    active.where(
                        (GenerationRun.user_id == actor_id)
                        | (GenerationRun.conversation_id == conversation_id)
                    ).subquery()
                )
            ):
                raise QueueFull("이미 진행 중인 응답이 있습니다. 완료 후 다시 보내세요.")
            count = await session.scalar(select(func.count()).select_from(active.subquery()))
            if count >= self.settings.generation_queue_limit + 1:
                raise QueueFull("생성 대기열이 가득 찼습니다. 잠시 후 다시 보내세요.")
            run_id = uuid4()
            await MonthlyAllowanceService(session, actor_id).ensure()
            quota = TokenQuotaService(session, actor_id)
            balance = await quota.get_balance()
            effective_options = options
            if not balance.unlimited:
                available_output = (balance.remaining_tokens or 0) - prompt_tokens
                if available_output < 1:
                    raise QuotaExceeded("질문과 답변에 사용할 토큰이 부족합니다.")
                effective_options = options.model_copy(
                    update={"max_tokens": min(options.max_tokens, available_output)}
                )
            authorized_tokens = prompt_tokens + effective_options.max_tokens
            if compaction_needed:
                authorized_tokens = self.settings.llm_context_window
                if not balance.unlimited:
                    authorized_tokens = min(authorized_tokens, balance.remaining_tokens or 0)
            # 문맥과 출력 한도만 확인하며 예산을 미리 예약하거나 차감하지 않는다.
            reservation = await quota.begin_deferred(
                request_key=f"generation:{run_id}",
                authorized_tokens=authorized_tokens,
            )
            user_message = await repository.append_message(
                conversation.workspace_id,
                conversation_id,
                role="user",
                content=content,
                model=conversation.model,
            )
            assistant = await repository.append_message(
                conversation.workspace_id,
                conversation_id,
                role="assistant",
                content="",
                status="pending",
                model=conversation.model,
            )
            if version == 1 and conversation.title == "새 대화":
                conversation.title = content[:60]
            run = GenerationRun(
                id=run_id,
                workspace_id=conversation.workspace_id,
                conversation_id=conversation_id,
                user_id=actor_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant.id,
                reservation_id=reservation.id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
                request_messages=[message.model_dump() for message in context],
                options=effective_options.model_dump(),
                prompt_tokens=prompt_tokens,
                context_compaction_needed=compaction_needed,
                max_output_tokens=effective_options.max_tokens,
                last_event_sequence=0,
            )
            session.add(run)
            await session.flush()
            add_event(
                session,
                run,
                "meta",
                {
                    "user_message_id": str(user_message.id),
                    "assistant_message_id": str(assistant.id),
                    "status": "queued",
                },
            )
            await session.commit()
            return run_payload(run)

    async def cancel(self, actor_id: UUID, run_id: UUID) -> dict:
        async with self.database.session() as session:
            await session.scalar(select(User).where(User.id == actor_id).with_for_update())
            run = await accessible_run(session, actor_id, run_id)
            if run.user_id != actor_id:
                raise AccessDenied("본인이 시작한 생성만 중단할 수 있습니다.")
            await session.scalar(
                select(Conversation).where(Conversation.id == run.conversation_id).with_for_update()
            )
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run.status in TERMINAL_STATUSES:
                return run_payload(run)
            if not run.cancel_requested:
                run.cancel_requested = True
                if run.status == "queued":
                    await TokenQuotaService(session, actor_id).release(
                        request_key=f"generation:{run.id}"
                    )
                    run.status, run.completed_at, run.request_messages = "cancelled", now(), []
                    assistant = await session.get(Message, run.assistant_message_id)
                    assistant.status = "cancelled"
                    add_event(session, run, "cancelled", {"input_tokens": 0, "output_tokens": 0})
                else:
                    add_event(session, run, "meta", {"status": "running", "cancel_requested": True})
            await session.commit()
            if signal := self.cancel_events.get(run_id):
                signal.set()
            return run_payload(run)

    async def append_chunk(self, run_id: UUID, chunk: str, *, offset: int | None = None) -> bool:
        async with self.database.session() as session:
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            )
            if run.status != "running":
                raise Conflict("생성 작업의 실행 상태가 변경되었습니다.")
            if chunk and not run.cancel_requested:
                assistant = await session.get(Message, run.assistant_message_id)
                if offset is None or len(assistant.content) == offset:
                    assistant.content += chunk
                    add_event(session, run, "delta", {"text": chunk})
                elif not (
                    len(assistant.content) == offset + len(chunk)
                    and assistant.content[offset:] == chunk
                ):
                    raise Conflict("응답 저장 위치가 일치하지 않습니다.")
            await session.commit()
            return run.cancel_requested

    async def recover_unsettled(self, actor_id: UUID | None = None) -> None:
        """이미 종료된 후정산 요청의 불명 사용량을 면제해 다음 질문을 허용한다."""
        async with self.database.session() as session:
            query = (
                select(GenerationRun.id)
                .join(TokenReservation, TokenReservation.id == GenerationRun.reservation_id)
                .where(
                    GenerationRun.status == "usage_pending",
                    TokenReservation.charge_mode == "deferred",
                    TokenReservation.status == "reserved",
                )
            )
            if actor_id is not None:
                query = query.where(GenerationRun.user_id == actor_id)
            ids = list((await session.scalars(query)).all())
        for run_id in ids:
            await self.finish(run_id)

    async def finish(
        self,
        run_id: UUID,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        error_code: str | None = None,
        never_started: bool = False,
        usage_basis: str = "provider",
        received_output_tokens: int | None = None,
        finish_reason: str | None = None,
        error_message: str | None = None,
    ) -> None:
        async with self.database.session() as session:
            initial = await session.get(GenerationRun, run_id)
            actor_id, conversation_id = initial.user_id, initial.conversation_id
            await session.scalar(select(User).where(User.id == actor_id).with_for_update())
            await session.scalar(
                select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            )
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            reservation = await session.get(TokenReservation, run.reservation_id)
            recovering = run.status == "usage_pending" and reservation.charge_mode == "deferred"
            if run.status in TERMINAL_STATUSES and not recovering:
                return
            if recovering:
                error_code = error_code or run.error_code
            assistant = await session.get(Message, run.assistant_message_id)
            quota = TokenQuotaService(session, actor_id)
            # 취소 감지 작업보다 전송 오류가 먼저 도착해도 DB에 확정된 중단을 우선한다.
            if (
                run.cancel_requested
                and input_tokens is None
                and output_tokens is None
                and type(received_output_tokens) is int
                and 0 <= received_output_tokens <= run.max_output_tokens
            ):
                input_tokens = run.prompt_tokens if received_output_tokens else 0
                output_tokens = received_output_tokens
                usage_basis = "received" if received_output_tokens else "waived"
            if usage_basis != "provider" and not run.cancel_requested:
                raise Conflict("수신량 기준 정산은 사용자가 중단한 요청에만 적용할 수 있습니다.")
            has_usage = (
                type(input_tokens) is int
                and type(output_tokens) is int
                and input_tokens >= 0
                and output_tokens >= 0
                and (
                    input_tokens == run.prompt_tokens
                    or (usage_basis == "waived" and input_tokens == output_tokens == 0)
                )
                and output_tokens <= run.max_output_tokens
            )
            if has_usage:
                await quota.settle(
                    request_key=f"generation:{run.id}",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_basis=usage_basis,
                )
                if run.cancel_requested:
                    run.status, assistant.status = "cancelled", "cancelled"
                    add_event(
                        session,
                        run,
                        "cancelled",
                        {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "usage_basis": usage_basis,
                        },
                    )
                elif error_code or not assistant.content.strip():
                    run.status, assistant.status = "failed", "failed"
                    error_code = error_code or "empty_response"
                    add_event(
                        session,
                        run,
                        "error",
                        {"message": "답변을 완료하지 못했습니다. 실제 사용량은 정산했습니다."},
                    )
                else:
                    run.status, assistant.status = "completed", "completed"
                    add_event(
                        session,
                        run,
                        "done",
                        {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "finish_reason": finish_reason
                            if finish_reason in ("stop", "length")
                            else None,
                        },
                    )
                assistant.token_count = output_tokens
            elif never_started:
                await quota.release(request_key=f"generation:{run.id}")
                run.status, assistant.status = "failed", "failed"
                add_event(
                    session,
                    run,
                    "error",
                    {
                        "message": error_message
                        or "모델을 실행하지 못했습니다. 토큰은 차감되지 않았습니다."
                    },
                )
            elif reservation.charge_mode == "deferred":
                # 서버 장애로 최종 사용량이 없으면 추정 청구 없이 실패 처리한다.
                await quota.release(request_key=f"generation:{run.id}")
                run.status, assistant.status = "failed", "failed"
                error_code = error_code or "usage_mismatch"
                add_event(
                    session,
                    run,
                    "error",
                    {
                        "message": (
                            "사용량을 확인하지 못한 요청은 차감 없이 종료했습니다. "
                            "다시 질문해 주세요."
                        ),
                        "usage_basis": "waived",
                        "charged_tokens": 0,
                    },
                )
            else:
                # 이전 예약 방식의 불명 사용량은 기존 예약을 보존한다.
                run.status, assistant.status = "usage_pending", "failed"
                error_code = error_code or "usage_mismatch"
                add_event(
                    session,
                    run,
                    "error",
                    {
                        "message": (
                            "모델 사용량을 확인하지 못해 차감을 보류했습니다. "
                            "사용량 확인 후 다시 질문할 수 있습니다."
                        )
                    },
                )
            run.error_code, run.request_messages = error_code, []
            # 복구 시각은 새 이벤트와 회계 기록에 남기고 원래 생성 종료 시각은 보존한다.
            if not recovering:
                run.completed_at = now()
            await session.commit()


class GenerationWorker:
    """PostgreSQL 세션 잠금으로 로컬 API 프로세스들 중 실행자 한 개만 추론을 담당한다."""

    def __init__(self, service: GenerationService):
        self.service = service
        self.task: asyncio.Task | None = None
        self.connection: asyncpg.Connection | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name="generation-worker")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def claim(self) -> dict | None:
        database = self.service.database
        async with database.session() as session:
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.status == "queued")
                .order_by(GenerationRun.created_at, GenerationRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            run.status, run.started_at = "running", now()
            add_event(session, run, "meta", {"status": "running"})
            result = {
                "id": run.id,
                "messages": list(run.request_messages),
                "options": dict(run.options),
                "user_id": run.user_id,
                "workspace_id": run.workspace_id,
                "conversation_id": run.conversation_id,
                "prompt_tokens": run.prompt_tokens,
                "context_compaction_needed": run.context_compaction_needed,
            }
            await session.commit()
            return result

    async def execute(self, job: dict) -> None:
        run_id, buffer, usage = job["id"], "", None
        last_flush, offset = monotonic(), 0
        received_output_tokens = 0
        cancelled = False
        finish_reason = None
        signal = self.service.cancel_events.setdefault(run_id, asyncio.Event())

        async def wait_for_cancel():
            while not signal.is_set():
                if self.connection is not None and self.connection.is_closed():
                    raise ProviderUnavailable("생성 실행자 연결이 끊어졌습니다.")
                # 다른 API 프로세스의 중단도 감지하되 대기 중 DB 연결을 점유하지 않는다.
                async with self.service.database.session() as session:
                    requested = await session.scalar(
                        select(GenerationRun.cancel_requested).where(GenerationRun.id == run_id)
                    )
                if requested:
                    return
                try:
                    await asyncio.wait_for(signal.wait(), timeout=0.1)
                except TimeoutError:
                    pass

        cancellation = asyncio.create_task(wait_for_cancel())

        def settlement():
            if usage is not None:
                return *usage, "provider"
            if cancelled:
                if received_output_tokens:
                    return job["prompt_tokens"], received_output_tokens, "received"
                return 0, 0, "waived"
            return None, None, "provider"

        async def flush_buffer():
            nonlocal buffer, offset, last_flush
            if buffer:
                await self.service.append_chunk(run_id, buffer, offset=offset)
                offset += len(buffer)
                buffer = ""
            last_flush = monotonic()

        async def finish_error(code: str, *, never_started: bool = False):
            try:
                await flush_buffer()
            finally:
                input_tokens, output_tokens, basis = settlement()
                await self.service.finish(
                    run_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_basis=basis,
                    received_output_tokens=received_output_tokens,
                    error_code=code,
                    never_started=never_started,
                )

        try:
            # 대기 중 비활성화·소속 철회된 계정은 추론 실행 전에 다시 차단한다.
            async with self.service.database.session() as session:
                await Repository(session, job["user_id"]).get_conversation(
                    job["workspace_id"], job["conversation_id"]
                )
            if job.get("context_compaction_needed"):
                try:
                    job = await CompactionService(self.service).prepare(job, cancellation)
                except CompactionCancelled:
                    cancelled = True
                    await finish_error("user_cancelled")
                    return
                except (InvalidInput, QuotaExceeded):
                    await self.service.finish(
                        run_id,
                        never_started=True,
                        received_output_tokens=0,
                        error_code="context_compaction_limit",
                        error_message=(
                            "현재 질문에 사용할 문맥이나 잔여 토큰이 부족합니다. "
                            "질문 또는 출력 길이를 줄여 주세요. 토큰은 차감되지 않았습니다."
                        ),
                    )
                    return
                except ProviderUnavailable:
                    await self.service.finish(
                        run_id,
                        never_started=True,
                        received_output_tokens=0,
                        error_code="context_compaction_failed",
                        error_message=(
                            "이전 대화를 정리하지 못했습니다. 다시 질문해 주세요. "
                            "기존 대화는 보존되었으며 토큰은 차감되지 않았습니다."
                        ),
                    )
                    return
            messages = [ChatMessage.model_validate(message) for message in job["messages"]]
            options = GenerationOptions.model_validate(job["options"])
            async with aclosing(self.service.provider.stream(messages, options)) as stream:
                pending = None
                try:
                    while True:
                        pending = asyncio.create_task(anext(stream))
                        completed, _ = await asyncio.wait(
                            (pending, cancellation), return_when=asyncio.FIRST_COMPLETED
                        )
                        if cancellation in completed:
                            cancellation.result()
                            cancelled = True
                        if pending in completed:
                            try:
                                delta = pending.result()
                            except StopAsyncIteration:
                                break
                            if self.connection is not None and self.connection.is_closed():
                                raise ProviderUnavailable("생성 실행자 연결이 끊어졌습니다.")
                            count = delta.received_output_tokens
                            if type(count) is int and 0 <= count <= options.max_tokens:
                                received_output_tokens = max(received_output_tokens, count)
                            if delta.final:
                                usage = (delta.input_tokens, delta.output_tokens)
                                finish_reason = delta.finish_reason
                            if not cancelled:
                                buffer += delta.text
                                if (
                                    len(buffer) >= 4096
                                    or monotonic() - last_flush >= 0.04
                                    or delta.final
                                ):
                                    await flush_buffer()
                        if cancelled:
                            break
                finally:
                    # 다음 토큰을 기다리는 HTTP 읽기를 먼저 취소해야 연결을 즉시 닫을 수 있다.
                    if pending is not None:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            await flush_buffer()
            # 전체 생성량을 추정하지 않고 확인된 수신량만 청구하며 나머지는 면제한다.
            input_tokens, output_tokens, usage_basis = settlement()
            await self.service.finish(
                run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_basis=usage_basis,
                received_output_tokens=received_output_tokens,
                finish_reason=finish_reason,
            )
        except asyncio.CancelledError:
            # 취소 직전 커밋한 청크는 offset으로 중복 저장하지 않고 확정 사용량도 보존한다.
            await asyncio.shield(finish_error("worker_stopped"))
            raise
        except AccessDenied:
            await finish_error("access_revoked", never_started=True)
        except ProviderUnavailable as error:
            await finish_error("provider_unavailable", never_started=not error.request_started)
        except Exception as error:
            logger.warning("생성 처리 실패", extra={"error_type": type(error).__name__})
            await finish_error("generation_failed")
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            self.service.cancel_events.pop(run_id, None)

    async def recover(self) -> None:
        async with self.service.database.session() as session:
            ids = list(
                (
                    await session.scalars(
                        select(GenerationRun.id).where(GenerationRun.status == "running")
                    )
                ).all()
            )
        # 이전 실행자의 DB 잠금이 사라진 뒤에도 모델 실제 사용량은 추측하지 않는다.
        for run_id in ids:
            await self.service.finish(run_id, error_code="worker_interrupted")
        await CompactionService(self.service).recover()
        await self.service.recover_unsettled()

    async def run(self) -> None:
        while True:
            try:
                settings = self.service.settings
                url = make_url(settings.database_url.get_secret_value()).set(
                    drivername="postgresql"
                )
                # API 풀과 별도 연결 하나를 써서 작은 풀에서도 SSE/업무 요청을 막지 않는다.
                self.connection = await asyncpg.connect(
                    url.render_as_string(hide_password=False),
                    timeout=5,
                    server_settings={"timezone": "UTC"},
                )
                leader = await self.connection.fetchval(
                    "SELECT pg_try_advisory_lock($1)", WORKER_LOCK
                )
                if leader:
                    await self.recover()
                    while not self.connection.is_closed():
                        job = await self.claim()
                        if job is None:
                            await asyncio.sleep(0.05)
                        else:
                            await self.execute(job)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "생성 실행자 재연결 대기", extra={"error_type": type(error).__name__}
                )
                await asyncio.sleep(2)
            finally:
                if self.connection is not None:
                    await self.connection.close(timeout=2)
                    self.connection = None
