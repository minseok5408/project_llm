"""생성 종료와 미확인 사용량 복구를 한 번만 정산한다."""

from uuid import UUID

from sqlalchemy import select

from backend.app.models import (
    Conversation,
    GenerationRun,
    Message,
    TokenReservation,
    User,
)
from backend.app.repositories import Conflict
from backend.app.services.generations.events import TERMINAL_STATUSES, add_event, now
from backend.app.services.token_quota import TokenQuotaService


class SettlementMixin:
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
