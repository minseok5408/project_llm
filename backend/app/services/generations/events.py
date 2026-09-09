"""생성 상태·공개 응답·접근 확인·이벤트 저장 규칙을 공유한다."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    GenerationEvent,
    GenerationRun,
)
from backend.app.repositories import AccessDenied, Repository

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "usage_pending")


def now() -> datetime:
    return datetime.now(UTC)


def run_payload(run: GenerationRun) -> dict:
    return {
        "id": str(run.id),
        "generation_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "user_message_id": str(run.user_message_id),
        "assistant_message_id": str(run.assistant_message_id),
        "supersedes_generation_id": str(run.supersedes_generation_id)
        if run.supersedes_generation_id
        else None,
        "status": run.status,
        "network_mode": run.network_mode,
        "web_search_mode": run.web_search_mode,
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
