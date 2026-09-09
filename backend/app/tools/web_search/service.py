"""단일 worker에서 검색 허용·중단·자료 예산과 출처 저장을 처리한다."""

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from backend.app.context.compaction import count_context
from backend.app.models import GenerationRun, Message, TokenReservation, User, WebSearchRun
from backend.app.repositories import Repository
from backend.app.runtime.cancellation import GenerationCancelled, cancellable
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.web_search.context import (
    SEARCH_CONTEXT_PROMPT,
    build_search_context,
    should_search,
)
from backend.app.tools.web_search.provider import SearchProviderError, SearchResponse

if TYPE_CHECKING:
    from backend.app.services.generations import GenerationService


class NetworkPermissionChanged(Exception):
    """요청 승인 뒤 로컬 설정이 바뀌어 외부 작업의 허용 범위가 끝났다."""


def search_payload(row: WebSearchRun | None) -> dict | None:
    if row is None:
        return None
    return {
        "status": row.status,
        "reason": row.reason,
        "provider": row.provider,
        "sources": row.sources,
    }


class WebSearchService:
    def __init__(self, service: "GenerationService"):
        self.service = service
        self.database, self.settings = service.database, service.settings

    async def _watch_permission(self, job: dict) -> None:
        # API와 실행자가 별도 프로세스이므로 DB 설정을 짧게 확인해야 한다.
        while True:
            if not await self.service.network_mode.is_allowed(
                job["user_id"], job["network_revision"]
            ):
                raise NetworkPermissionChanged
            await asyncio.sleep(0.05)

    async def _guarded(self, operation, job: dict, cancellation: asyncio.Task):
        """이미 만든 코루틴도 허용 검사 실패 때 닫고 모든 대기 작업을 회수한다."""
        try:
            if cancellation.done():
                cancellation.result()
                raise GenerationCancelled
            permitted = await self.service.network_mode.is_allowed(
                job["user_id"], job["network_revision"]
            )
            if cancellation.done():
                cancellation.result()
                raise GenerationCancelled
        except BaseException:
            operation.close()
            raise
        if not permitted:
            operation.close()
            raise NetworkPermissionChanged
        pending = asyncio.create_task(operation)
        guard = asyncio.create_task(self._watch_permission(job))
        try:
            done, _ = await asyncio.wait(
                (pending, guard, cancellation), return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation in done:
                cancellation.result()
                raise GenerationCancelled
            if guard in done:
                guard.result()
            result = pending.result()
            if not await self.service.network_mode.is_allowed(
                job["user_id"], job["network_revision"]
            ):
                raise NetworkPermissionChanged
            return result
        finally:
            pending.cancel()
            guard.cancel()
            await asyncio.gather(pending, guard, return_exceptions=True)

    async def _record(
        self, job: dict, status: str, reason: str | None = None, sources: list | None = None
    ) -> dict | None:
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if run.status != "running":
                raise GenerationCancelled
            if run.cancel_requested and status != "cancelled":
                raise GenerationCancelled
            row = await session.scalar(
                select(WebSearchRun).where(WebSearchRun.generation_id == run.id)
            )
            if row is None:
                row = WebSearchRun(
                    generation_id=run.id,
                    provider=self.service.search_provider.name,
                    status=status,
                    sources=[],
                )
                session.add(row)
            row.status, row.reason, row.sources = status, reason, sources or []
            row.completed_at = None if status in ("pending", "searching") else datetime.now(UTC)
            payload = search_payload(row)
            add_event(
                session,
                run,
                "meta",
                {
                    "stage": "searching" if status == "searching" else "generating",
                    "search": payload,
                },
            )
            await session.commit()
            return payload

    async def _close_pending(self, job: dict, status: str, reason: str) -> None:
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            row = await session.scalar(
                select(WebSearchRun).where(WebSearchRun.generation_id == job["id"])
            )
            if row is not None and row.status in ("pending", "searching"):
                row.status, row.reason, row.sources = status, reason, []
                row.completed_at = datetime.now(UTC)
                if run.status == "running":
                    add_event(
                        session, run, "meta", {"stage": "generating", "search": search_payload(row)}
                    )
                await session.commit()

    async def prepare(self, job: dict, cancellation: asyncio.Task) -> dict:
        try:
            return await self._prepare(job, cancellation)
        except GenerationCancelled:
            await self._close_pending(job, "cancelled", "user_cancelled")
            raise
        except asyncio.CancelledError:
            await asyncio.shield(self._close_pending(job, "cancelled", "worker_stopped"))
            raise
        except Exception:
            await self._close_pending(job, "failed", "preparation_failed")
            raise

    async def _store_context(
        self,
        job: dict,
        messages: list[ChatMessage],
        options: GenerationOptions,
        tokens: int,
        *,
        sources: list | None = None,
    ) -> dict:
        from backend.app.services.generations.events import add_event

        async with self.database.session() as session:
            if sources is not None:
                # 설정 변경과 출처 채택을 같은 사용자 잠금으로 직렬화한다.
                await session.scalar(
                    select(User).where(User.id == job["user_id"]).with_for_update()
                )
                preference = await self.service.network_mode.preference(session, job["user_id"])
                if preference.local_only or preference.revision != job["network_revision"]:
                    raise NetworkPermissionChanged
            await Repository(session, job["user_id"]).get_conversation(
                job["workspace_id"], job["conversation_id"]
            )
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
            )
            if run.status != "running" or run.cancel_requested:
                raise GenerationCancelled
            run.request_messages = [message.model_dump() for message in messages]
            run.options = options.model_dump()
            run.prompt_tokens, run.max_output_tokens = tokens, options.max_tokens
            if sources is not None:
                row = await session.scalar(
                    select(WebSearchRun).where(WebSearchRun.generation_id == run.id)
                )
                row.status, row.reason, row.sources = "completed", None, sources
                row.completed_at = datetime.now(UTC)
                add_event(
                    session, run, "meta", {"stage": "generating", "search": search_payload(row)}
                )
            await session.commit()
            return {
                **job,
                "messages": [message.model_dump() for message in messages],
                "options": options.model_dump(),
                "prompt_tokens": tokens,
            }

    async def _fit(self, job: dict, response: SearchResponse):
        base = [ChatMessage.model_validate(message) for message in job["messages"]]
        options = GenerationOptions.model_validate(job["options"])
        async with self.database.session() as session:
            run = await session.get(GenerationRun, job["id"])
            reservation = await session.get(TokenReservation, run.reservation_id)
            cap = min(reservation.reserved_tokens, self.settings.llm_context_window)
        results = response.results[: self.settings.web_search_max_results]
        # 상위 결과부터 하나씩 줄여 자료 때문에 답변할 최소 공간이 사라지지 않게 한다.
        for amount in range(len(results), 0, -1):
            selected = results[:amount]
            reference = build_search_context(
                selected, max_chars=self.settings.web_search_max_context_chars
            )
            if reference is None:
                continue
            messages = [base[0], reference, *base[1:]]
            if (
                sum(len(message.content) for message in messages)
                > self.settings.llm_max_history_chars
            ):
                continue
            tokens = await count_context(self.service.provider, messages, options)
            if tokens + min(64, options.max_tokens) > cap:
                continue
            effective = options.model_copy(
                update={"max_tokens": min(options.max_tokens, cap - tokens)}
            )
            # 문맥 생성기가 문자 예산으로 덜어낸 출처는 실제 사용 목록에 넣지 않는다.
            records = json.loads(reference.content[len(SEARCH_CONTEXT_PROMPT) :])["sources"]
            sources = [
                {
                    "number": record["id"],
                    "title": record["title"],
                    "url": record["url"],
                    "snippet": record["snippet"],
                    "retrieved_at": response.checked_at.isoformat(),
                }
                for record in records
            ]
            return messages, effective, tokens, sources
        return None

    async def _fallback(
        self, job: dict, status: str, reason: str, cancellation: asyncio.Task
    ) -> dict:
        await self._record(job, status, reason)
        base = [ChatMessage.model_validate(message) for message in job["messages"]]
        notice = ChatMessage(
            role="system",
            content=(
                "이번 답변에는 새 인터넷 검색 자료를 제공하지 못했습니다. "
                "웹을 검색하거나 최신 사실을 확인했다고 말하지 마세요. "
                "기존 지식으로 답할 수 있는 범위를 설명하고 최신 정보가 필요하면 "
                "확인하지 못했다고 밝혀 주세요."
            ),
        )
        messages = [base[0], notice, *base[1:]]
        options = GenerationOptions.model_validate(job["options"])
        if sum(len(message.content) for message in messages) > self.settings.llm_max_history_chars:
            return job
        tokens = await cancellable(
            count_context(self.service.provider, messages, options), cancellation
        )
        async with self.database.session() as session:
            run = await session.get(GenerationRun, job["id"])
            reservation = await session.get(TokenReservation, run.reservation_id)
            cap = min(reservation.reserved_tokens, self.settings.llm_context_window)
        if tokens + min(64, options.max_tokens) > cap:
            return job
        options = options.model_copy(update={"max_tokens": min(options.max_tokens, cap - tokens)})
        return await self._store_context(job, messages, options, tokens)

    async def _prepare(self, job: dict, cancellation: asyncio.Task) -> dict:
        async with self.database.session() as session:
            run = await session.get(GenerationRun, job["id"])
            content = (await session.get(Message, run.user_message_id)).content
        mode = job.get("web_search_mode", "off")
        if not should_search(content, "on" if mode == "on" else "auto"):
            return job
        try:
            if job.get("network_mode", "local") == "local" or mode == "off":
                return await self._fallback(
                    job,
                    "disabled",
                    "forced_local" if job.get("network_mode") == "local" else "search_off",
                    cancellation,
                )
            if not self.service.search_provider.configured:
                return await self._fallback(
                    job, "unavailable", "provider_unconfigured", cancellation
                )
            await self._record(job, "searching")
            status = await self._guarded(
                self.service.network_mode.status(job["user_id"]), job, cancellation
            )
            if status["mode"] != "online":
                return await self._fallback(job, "unavailable", status["reason"], cancellation)
            response = await self._guarded(
                self.service.search_provider.search(
                    content[: self.settings.web_search_max_query_chars]
                ),
                job,
                cancellation,
            )
            if not response.results:
                return await self._fallback(job, "no_results", "no_results", cancellation)
            fitted = await self._guarded(self._fit(job, response), job, cancellation)
            if fitted is None:
                return await self._fallback(job, "omitted", "context_limit", cancellation)
            messages, options, tokens, sources = fitted
            return await self._store_context(job, messages, options, tokens, sources=sources)
        except NetworkPermissionChanged:
            return await self._fallback(job, "disabled", "mode_changed", cancellation)
        except SearchProviderError as error:
            return await self._fallback(job, "failed", error.code, cancellation)

    async def recover(self) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(WebSearchRun)
                .where(
                    WebSearchRun.status.in_(("pending", "searching")),
                    WebSearchRun.generation_id.in_(
                        select(GenerationRun.id).where(
                            GenerationRun.status.not_in(("queued", "running"))
                        )
                    ),
                )
                .values(
                    status="cancelled",
                    reason="worker_interrupted",
                    sources=[],
                    completed_at=datetime.now(UTC),
                )
            )
            await session.commit()
