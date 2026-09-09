"""새 질문·재생성의 권한·문맥·예산 승인과 원문 저장을 담당한다."""

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.context.builder import (
    compose_context,
    list_context_turns,
)
from backend.app.context.compaction import count_context
from backend.app.context.service import latest_summary
from backend.app.models import (
    Conversation,
    GenerationRun,
    Message,
    User,
)
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, Repository
from backend.app.repositories.core import logged, required_text
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.generations.events import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    accessible_run,
    add_event,
    run_payload,
)
from backend.app.services.monthly_allowance import MonthlyAllowanceService
from backend.app.services.token_quota import QuotaExceeded, TokenQuotaService
from backend.app.tools.web_search.context import should_search

ADMISSION_LOCK = 7160524630128422


class QueueFull(Conflict):
    """현재 계정 또는 전체 생성 대기열에 여유가 없다."""


async def context_messages(
    session: AsyncSession, conversation: Conversation, content: str
) -> list[ChatMessage]:
    summary, through = await latest_summary(session, conversation)
    turns = await list_context_turns(session, conversation, after_sequence=through)
    return compose_context(turns, content, summary)


class AdmissionMixin:
    async def regenerate(
        self,
        actor_id: UUID,
        run_id: UUID,
        *,
        options: GenerationOptions,
        idempotency_key: UUID,
        network_mode: str | None = None,
        web_search: str = "auto",
    ) -> dict:
        """마지막 질문의 원문은 재사용하고 답변과 사용량은 새 버전으로 기록한다."""
        async with self.database.session() as session:
            run = await accessible_run(session, actor_id, run_id)
            if run.user_id != actor_id:
                raise AccessDenied("본인이 시작한 답변만 다시 생성할 수 있습니다.")
            content = (await session.get(Message, run.user_message_id)).content
            conversation_id = run.conversation_id
        return await self.submit(
            actor_id,
            conversation_id,
            content=content,
            options=options,
            idempotency_key=idempotency_key,
            supersedes_generation_id=run_id,
            network_mode=network_mode,
            web_search=web_search,
        )

    async def _regeneration_target(
        self, session: AsyncSession, actor_id: UUID, conversation: Conversation, run_id: UUID
    ) -> GenerationRun:
        run = await accessible_run(session, actor_id, run_id)
        if run.user_id != actor_id or run.conversation_id != conversation.id:
            raise AccessDenied("본인이 시작한 답변만 다시 생성할 수 있습니다.")
        latest_user_id = await session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation.id, Message.role == "user")
            .order_by(Message.sequence.desc())
            .limit(1)
        )
        if (
            not run.is_current
            or run.status not in TERMINAL_STATUSES
            or run.user_message_id != latest_user_id
        ):
            raise Conflict("가장 최근 질문의 현재 답변만 다시 생성할 수 있습니다.")
        _, through = await latest_summary(session, conversation)
        user_message = await session.get(Message, run.user_message_id)
        if user_message.sequence <= through:
            raise Conflict("이미 요약에 포함된 질문은 다시 생성할 수 없습니다.")
        return run

    @logged
    async def submit(
        self,
        actor_id: UUID,
        conversation_id: UUID,
        *,
        content: str,
        options: GenerationOptions,
        idempotency_key: UUID,
        supersedes_generation_id: UUID | None = None,
        network_mode: str | None = None,
        web_search: str = "auto",
    ) -> dict:
        content = required_text(content, 100_000, "메시지")
        if network_mode not in (None, "auto", "local") or web_search not in ("auto", "on", "off"):
            raise InvalidInput("네트워크 또는 검색 설정이 올바르지 않습니다.")
        await self.recover_unsettled(actor_id)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "conversation_id": str(conversation_id),
                    "content": content,
                    "options": options.model_dump(),
                    **({"network_mode": network_mode} if network_mode is not None else {}),
                    **({"web_search": web_search} if web_search != "auto" else {}),
                    **(
                        {"supersedes_generation_id": str(supersedes_generation_id)}
                        if supersedes_generation_id
                        else {}
                    ),
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
            if supersedes_generation_id:
                previous = await self._regeneration_target(
                    session, actor_id, conversation, supersedes_generation_id
                )
                original = await session.get(Message, previous.user_message_id)
                # 다시 답할 질문과 이전 답변은 이력에서 빼고 현재 사용자 입력으로 한 번만 넣는다.
                turns = [turn for turn in turns if turn.user_sequence != original.sequence]
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
            previous = None
            if supersedes_generation_id:
                previous = await self._regeneration_target(
                    session, actor_id, conversation, supersedes_generation_id
                )
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
            preference = await self.network_mode.preference(session, actor_id)
            resolved_mode = "local" if preference.local_only or network_mode == "local" else "auto"
            search_possible = (
                resolved_mode == "auto"
                and self.search_provider.configured
                and should_search(content, web_search)
            )
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
            if compaction_needed or search_possible:
                authorized_tokens = self.settings.llm_context_window
                if not balance.unlimited:
                    authorized_tokens = min(authorized_tokens, balance.remaining_tokens or 0)
            # 문맥과 출력 한도만 확인하며 예산을 미리 예약하거나 차감하지 않는다.
            reservation = await quota.begin_deferred(
                request_key=f"generation:{run_id}",
                authorized_tokens=authorized_tokens,
            )
            if previous is None:
                user_message = await repository.append_message(
                    conversation.workspace_id,
                    conversation_id,
                    role="user",
                    content=content,
                    model=conversation.model,
                )
            else:
                user_message = await session.get(Message, previous.user_message_id)
                previous.is_current = False
                # 현재 버전 유일 제약을 먼저 해제하되 전체 요청은 한 트랜잭션으로 확정한다.
                await session.flush()
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
                supersedes_generation_id=supersedes_generation_id,
                reservation_id=reservation.id,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
                request_messages=[message.model_dump() for message in context],
                options=effective_options.model_dump(),
                prompt_tokens=prompt_tokens,
                context_compaction_needed=compaction_needed,
                network_mode=resolved_mode,
                network_revision=preference.revision,
                web_search_mode=web_search,
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
