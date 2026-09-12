"""단계별 실행 한도·권한·실제 사용량을 기록하고 생성 종료 시 합산한다."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.context.dependencies import check_dependencies
from backend.app.db import Database
from backend.app.models import GenerationRun, GenerationStep, TokenReservation
from backend.app.repositories import Repository
from backend.app.runtime.cancellation import GenerationCancelled
from backend.app.runtime.contracts import GenerationJob
from backend.app.runtime.progress import advance_progress


class StepLimitExceeded(Exception):
    """단계 또는 누적 입출력 허용량이 부족하다."""


def step_payload(step: GenerationStep) -> dict:
    # 프롬프트·도구 인자·외부 검색 원문은 관측 이벤트에 복제하지 않는다.
    return {
        "id": str(step.id),
        "sequence": step.sequence,
        "kind": step.kind,
        "name": step.name,
        "status": step.status,
        "reason": step.reason,
        "input_tokens": step.input_tokens,
        "output_tokens": step.output_tokens,
        "usage_basis": step.usage_basis,
    }


class StepService:
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings

    async def remaining(self, job: GenerationJob) -> int:
        async with self.database.session() as session:
            run = await session.get(GenerationRun, job["id"])
            reservation = await session.get(TokenReservation, run.reservation_id)
            spent = await session.scalar(
                select(func.coalesce(func.sum(GenerationStep.budget_tokens), 0)).where(
                    GenerationStep.generation_id == run.id
                )
            )
            # PostgreSQL의 bigint 합계는 numeric으로 반환되므로 생성 옵션은 정수로 유지한다.
            return max(0, reservation.reserved_tokens - int(spent))

    async def start(
        self,
        job: GenerationJob,
        *,
        kind: str,
        name: str,
        prompt_tokens: int = 0,
        max_output_tokens: int = 0,
        keep_tokens: int = 0,
        call_id: str | None = None,
    ) -> UUID:
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            await Repository(session, job["user_id"]).get_conversation(
                job["workspace_id"], job["conversation_id"]
            )
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if (
                run.status != "running"
                or run.cancel_requested
                or not await check_dependencies(session, run.memory_dependencies)
            ):
                raise GenerationCancelled
            rows = list(
                (
                    await session.scalars(
                        select(GenerationStep).where(GenerationStep.generation_id == run.id)
                    )
                ).all()
            )
            reservation = await session.get(TokenReservation, run.reservation_id)
            # 마지막 답변 한 단계의 자리와 최소 토큰은 준비 단계에서 남겨 둔다.
            limit = self.settings.generation_max_steps - (name != "answer")
            needed = prompt_tokens + max_output_tokens
            if (
                len(rows) >= limit
                or sum(row.budget_tokens for row in rows) + needed + keep_tokens
                > reservation.reserved_tokens
            ):
                raise StepLimitExceeded
            if any(row.status == "running" for row in rows):
                raise StepLimitExceeded
            step = GenerationStep(
                id=uuid4(),
                generation_id=run.id,
                sequence=len(rows) + 1,
                kind=kind,
                name=name,
                call_id=call_id,
                status="running",
                prompt_tokens=prompt_tokens,
                max_output_tokens=max_output_tokens,
                budget_tokens=needed,
                input_tokens=0,
                output_tokens=0,
                usage_basis="waived",
            )
            session.add(step)
            advance_progress(
                run, "answer" if name == "answer" else str(step.id), "running", name=name
            )
            add_event(session, run, "meta", {"step": step_payload(step), "progress": run.progress})
            await session.commit()
            return step.id

    async def close(
        self,
        job: GenerationJob,
        step_id: UUID,
        *,
        status: str,
        reason: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        received_output_tokens: int = 0,
        call_id: str | None = None,
    ) -> bool:
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            step = await session.get(GenerationStep, step_id)
            if step.generation_id != run.id or step.status != "running":
                return False
            valid = complete_step(
                step,
                cancelled=run.cancel_requested,
                status=status,
                reason=reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                received_output_tokens=received_output_tokens,
            )
            if call_id:
                step.call_id = call_id[:200]
            if run.status == "running":
                # 최종 답변의 완료 표시는 본문·질문 카드·정산이 함께 저장된 뒤 확정한다.
                if step.name != "answer":
                    advance_progress(run, str(step.id), step.status, name=step.name)
                add_event(
                    session, run, "meta", {"step": step_payload(step), "progress": run.progress}
                )
            await session.commit()
            return valid


def complete_step(
    step: GenerationStep,
    *,
    cancelled: bool,
    status: str,
    reason: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    received_output_tokens: int = 0,
) -> bool:
    valid = (
        type(input_tokens) is int
        and type(output_tokens) is int
        and input_tokens == step.prompt_tokens
        and 0 <= output_tokens <= step.max_output_tokens
    )
    if step.kind == "tool":
        step.budget_tokens = 0
    elif valid:
        step.input_tokens, step.output_tokens = input_tokens, output_tokens
        step.usage_basis = "provider"
        step.budget_tokens = input_tokens + output_tokens
    elif (
        cancelled
        and type(received_output_tokens) is int
        and 0 < received_output_tokens <= step.max_output_tokens
    ):
        step.input_tokens, step.output_tokens = step.prompt_tokens, received_output_tokens
        step.usage_basis = "received"
        step.budget_tokens = step.input_tokens + step.output_tokens
    # 불명 사용량은 청구하지 않지만 같은 생성에서 재실행으로 한도를 우회하지 못하게 한다.
    if step.kind == "llm" and not valid and status == "completed":
        status, reason = "failed", "usage_mismatch"
    if cancelled:
        status = "cancelled"
    step.status, step.reason, step.completed_at = status, reason, datetime.now(UTC)
    return valid
