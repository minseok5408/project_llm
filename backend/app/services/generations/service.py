"""생성 서비스의 의존성과 중단·청크 저장을 연결한다."""

import asyncio
from uuid import UUID

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.llm.protocol import ChatProvider
from backend.app.models import (
    Conversation,
    GenerationRun,
    Message,
    User,
)
from backend.app.repositories import AccessDenied, Conflict
from backend.app.services.generations.admission import AdmissionMixin
from backend.app.services.generations.events import (
    TERMINAL_STATUSES,
    accessible_run,
    add_event,
    now,
    run_payload,
)
from backend.app.services.generations.settlement import SettlementMixin
from backend.app.services.network_mode import NetworkModeService
from backend.app.services.token_quota import TokenQuotaService
from backend.app.tools.web_search.provider import build_search_provider


class GenerationService(AdmissionMixin, SettlementMixin):
    def __init__(self, database: Database, provider: ChatProvider, settings: Settings):
        self.database, self.provider, self.settings = database, provider, settings
        self.cancel_events: dict[UUID, asyncio.Event] = {}
        self.search_provider = build_search_provider(settings)
        self.network_mode = NetworkModeService(database, settings, self.search_provider)

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
