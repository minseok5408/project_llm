"""정산과 분리된 요청별 문맥 측정값을 이벤트에 보존하고 공개한다."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.models import (
    ConversationCompaction,
    GenerationEvent,
    GenerationRun,
    TokenReservation,
)


def context_measurement(
    run: GenerationRun, settings: Settings, *, through: int, phase: str
) -> dict:
    """압축 전 최소 승인 입력은 실제 답변의 입력량으로 공개하지 않는다."""
    return {
        "generation_id": str(run.id),
        "phase": phase,
        "input_tokens": None if run.context_compaction_needed else run.prompt_tokens,
        "output_tokens": None,
        "max_output_tokens": run.max_output_tokens,
        "context_window": settings.llm_context_window,
        "summary_through_sequence": through if not run.context_compaction_needed else None,
        "compaction_status": "pending" if run.context_compaction_needed else "idle",
    }


async def context_status(session: AsyncSession, run: GenerationRun) -> dict | None:
    """과거 요청은 당시 저장한 한도와 요약 범위를 사용하며 현재 설정으로 추정하지 않는다."""
    event = await session.scalar(
        select(GenerationEvent)
        .where(
            GenerationEvent.generation_id == run.id,
            GenerationEvent.kind == "meta",
            GenerationEvent.payload.has_key("context"),
        )
        .order_by(GenerationEvent.sequence.desc())
        .limit(1)
    )
    if event is None:
        return None
    result = dict(event.payload["context"])
    attempt = await session.scalar(
        select(ConversationCompaction)
        .where(ConversationCompaction.generation_id == run.id)
        .order_by(ConversationCompaction.created_at.desc(), ConversationCompaction.id.desc())
        .limit(1)
    )
    if attempt is not None:
        result["compaction_status"] = attempt.status
    terminal = run.status not in ("queued", "running")
    if run.context_compaction_needed and terminal:
        result["compaction_status"] = "cancelled" if run.status == "cancelled" else "failed"
    elif run.context_compaction_needed and run.status == "running":
        result["compaction_status"] = "running"
    if terminal:
        result["phase"] = "finished"
        reservation = await session.get(TokenReservation, run.reservation_id)
        if reservation and reservation.usage_basis in ("provider", "received"):
            result["output_tokens"] = reservation.output_tokens
    return result
