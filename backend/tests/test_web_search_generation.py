"""독립 PostgreSQL에서 검색·모드 변경·중단과 실제 답변 사용량 정산을 검증한다."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.llm.protocol import ProviderUnavailable
from backend.app.llm.providers.mock import MockProvider
from backend.app.models import WebSearchRun
from backend.app.schemas import GenerationOptions
from backend.app.services.network_mode import NetworkModeService
from backend.app.tools.web_search.context import SEARCH_CONTEXT_PROMPT
from backend.app.tools.web_search.provider import (
    SearchProviderError,
    SearchResponse,
    SearchResult,
)
from backend.tests.test_generations import Harness, balance, snapshot
from backend.tests.test_generations import harness as harness

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class FakeSearchProvider:
    """외부 요청 없이 검색 대기·연결 실패·취소를 제어한다."""

    name = "brave"
    configured = True

    def __init__(self):
        self.reachable = True
        self.error: str | None = None
        self.blocked = False
        self.check_calls = 0
        self.search_calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.results = (
            SearchResult(
                "Python 공식 문서",
                "https://docs.python.org/3/",
                "Python 공식 문서에서 언어 기능과 표준 라이브러리 정보를 확인할 수 있습니다.",
            ),
        )

    async def check(self) -> bool:
        self.check_calls += 1
        return self.reachable

    async def search(self, query: str) -> SearchResponse:
        self.search_calls.append(query)
        self.started.set()
        try:
            if self.blocked:
                await self.release.wait()
            if self.error:
                raise SearchProviderError(self.error)
            return SearchResponse(self.results, datetime.now(UTC))
        finally:
            self.closed.set()


class RecordingChatProvider(MockProvider):
    """실제 모의 토큰 계산·완료 응답을 유지하며 생성 시작과 입력을 관찰한다."""

    def __init__(self, settings):
        super().__init__(settings, delay_seconds=0)
        self.calls = []
        self.blocked = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.reference_tokens: int | None = None
        self.reference_error: Exception | None = None

    async def count_input(self, messages, options):
        has_reference = any(
            message.content.startswith(SEARCH_CONTEXT_PROMPT) for message in messages
        )
        if has_reference and self.reference_error is not None:
            raise self.reference_error
        if has_reference and self.reference_tokens is not None:
            return self.reference_tokens
        return await super().count_input(messages, options)

    async def stream(self, messages, options):
        self.calls.append((list(messages), options))
        self.started.set()
        if self.blocked:
            await self.release.wait()
        async for delta in super().stream(messages, options):
            yield delta


def install(harness: Harness) -> tuple[FakeSearchProvider, RecordingChatProvider]:
    search = FakeSearchProvider()
    model = RecordingChatProvider(harness.settings)
    harness.provider = harness.service.provider = model
    harness.service.search_provider = search
    harness.service.network_mode = NetworkModeService(harness.database, harness.settings, search)
    return search, model


async def search_record(harness: Harness, generation_id: UUID | str) -> WebSearchRun | None:
    async with harness.database.session() as session:
        row = await session.scalar(
            select(WebSearchRun).where(WebSearchRun.generation_id == UUID(str(generation_id)))
        )
        session.expunge_all()
        return row


async def finish_task(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_search_completion_preserves_sources_and_charges_only_final_exact_usage(
    harness: Harness,
) -> None:
    search, model = install(harness)
    model.blocked = True
    await harness.grant(limit=10_000)
    content = "최신 Python 정보를 검색해줘"
    request = await harness.submit(harness.member, content=content)
    pending = await snapshot(harness.database, request["id"])
    assert search.check_calls == 0 and search.search_calls == []
    before = await balance(harness.database, harness.member)
    assert before.used_tokens == before.reserved_tokens == 0
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(model.started.wait(), timeout=5)
        during = await balance(harness.database, harness.member)
        assert during.used_tokens == during.reserved_tokens == 0
        row = await search_record(harness, request["id"])
        assert row.status == "completed"
        assert [source["number"] for source in row.sources] == [1]
        assert row.sources[0]["url"] == search.results[0].url
        assert row.sources[0]["snippet"] == search.results[0].snippet
        assert row.sources[0]["retrieved_at"]
        model.release.set()
        await asyncio.wait_for(task, timeout=5)
    finally:
        await finish_task(task)

    complete = await snapshot(harness.database, request["id"])
    messages, options = model.calls[0]
    exact_input = await model.count_input(messages, options)
    assert len(model.calls) == 1
    assert search.search_calls == [content]
    assert complete.run.status == complete.assistant.status == "completed"
    assert complete.user_message.content == content
    assert complete.reservation.status == "settled"
    assert complete.reservation.input_tokens == complete.run.prompt_tokens == exact_input
    assert complete.run.prompt_tokens > pending.run.prompt_tokens
    assert complete.reservation.output_tokens == complete.assistant.token_count
    after = await balance(harness.database, harness.member)
    assert after.used_tokens == exact_input + complete.assistant.token_count
    assert after.reserved_tokens == 0
    assert any(
        event.kind == "meta" and event.payload.get("search", {}).get("status") == "completed"
        for event in complete.events
    )


@pytest.mark.parametrize("content", ["파이썬 리스트를 설명해줘", "현재 내 이름과 직업이 뭐였지?"])
async def test_auto_without_web_need_performs_no_external_io(
    harness: Harness, content: str
) -> None:
    search, model = install(harness)
    request = await harness.submit(content=content)
    await harness.execute_next()
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    assert search.check_calls == 0 and search.search_calls == []
    assert await search_record(harness, request["id"]) is None
    assert len(model.calls) == 1


@pytest.mark.parametrize("forced_by", ["request", "preference", "search_off"])
async def test_forced_local_and_search_off_make_no_external_request(
    harness: Harness, forced_by: str
) -> None:
    search, model = install(harness)
    arguments = {}
    if forced_by == "request":
        arguments["network_mode"] = "local"
    elif forced_by == "preference":
        await harness.service.network_mode.set_local_only(harness.system.user_id, True)
    else:
        arguments["web_search"] = "off"
    request = await harness.submit(content="최신 Python 소식을 검색해줘", **arguments)
    await harness.execute_next()
    row = await search_record(harness, request["id"])
    assert row.status == "disabled"
    assert row.reason == ("search_off" if forced_by == "search_off" else "forced_local")
    assert row.sources == []
    assert search.check_calls == 0 and search.search_calls == []
    assert len(model.calls) == 1
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"


@pytest.mark.parametrize(
    "outcome,status,reason",
    [
        ("offline", "unavailable", "offline"),
        ("rate_limited", "failed", "rate_limited"),
        ("no_results", "no_results", "no_results"),
    ],
)
async def test_search_failure_falls_back_to_local_answer_with_honest_context(
    harness: Harness, outcome: str, status: str, reason: str
) -> None:
    search, model = install(harness)
    if outcome == "offline":
        search.reachable = False
    elif outcome == "no_results":
        search.results = ()
    else:
        search.error = outcome
    request = await harness.submit(content="최신 Python 뉴스를 검색해줘")
    await harness.execute_next()
    row = await search_record(harness, request["id"])
    assert row.status == status and row.reason == reason and row.sources == []
    assert search.check_calls == 1
    assert len(search.search_calls) == (0 if outcome == "offline" else 1)
    assert len(model.calls) == 1
    messages, options = model.calls[0]
    assert any("확인했다고 말하지" in message.content for message in messages)
    assert not any(message.content.startswith(SEARCH_CONTEXT_PROMPT) for message in messages)
    complete = await snapshot(harness.database, request["id"])
    assert complete.run.status == "completed"
    assert complete.reservation.input_tokens == await model.count_input(messages, options)


async def test_unconfigured_search_never_checks_or_searches(harness: Harness) -> None:
    search, model = install(harness)
    search.configured = False
    request = await harness.submit(content="최신 정보를 검색해줘")
    await harness.execute_next()
    row = await search_record(harness, request["id"])
    assert row.status == "unavailable" and row.reason == "provider_unconfigured"
    assert search.check_calls == 0 and search.search_calls == []
    assert len(model.calls) == 1


async def test_cancel_during_search_closes_search_and_never_starts_or_charges_model(
    harness: Harness,
) -> None:
    search, model = install(harness)
    search.blocked = True
    await harness.grant()
    request = await harness.submit(harness.member, content="최신 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(search.started.wait(), timeout=5)
        await harness.service.cancel(harness.member.user_id, UUID(request["id"]))
        await asyncio.wait_for(task, timeout=5)
    finally:
        await finish_task(task)
    assert search.closed.is_set()
    assert model.calls == []
    complete = await snapshot(harness.database, request["id"])
    assert complete.run.status == "cancelled"
    assert complete.assistant.content == ""
    assert complete.reservation.input_tokens == complete.reservation.output_tokens == 0
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0
    row = await search_record(harness, request["id"])
    assert row.status == "cancelled" and row.sources == []


async def test_local_switch_during_search_cancels_external_work_and_finishes_locally(
    harness: Harness,
) -> None:
    search, model = install(harness)
    search.blocked = True
    request = await harness.submit(content="최신 문서를 검색해줘")
    task = asyncio.create_task(harness.execute_next())
    try:
        await asyncio.wait_for(search.started.wait(), timeout=5)
        preference = await harness.service.network_mode.set_local_only(harness.system.user_id, True)
        assert preference["local_only"] is True and preference["revision"] == 1
        await asyncio.wait_for(task, timeout=5)
    finally:
        await finish_task(task)
    assert search.closed.is_set()
    assert search.check_calls == 1 and len(search.search_calls) == 1
    assert len(model.calls) == 1
    row = await search_record(harness, request["id"])
    assert row.status == "disabled" and row.reason == "mode_changed" and row.sources == []
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    assert not any(
        message.content.startswith(SEARCH_CONTEXT_PROMPT) for message in model.calls[0][0]
    )


async def test_local_switch_while_queued_prevents_every_external_request(harness: Harness) -> None:
    search, model = install(harness)
    request = await harness.submit(content="최신 문서를 검색해줘")
    queued = await snapshot(harness.database, request["id"])
    assert queued.run.network_mode == "auto" and queued.run.network_revision == 0
    await harness.service.network_mode.set_local_only(harness.system.user_id, True)
    await harness.execute_next()
    assert search.check_calls == 0 and search.search_calls == []
    assert len(model.calls) == 1
    row = await search_record(harness, request["id"])
    assert row.status == "disabled" and row.reason == "mode_changed"
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"


async def test_sources_that_exceed_token_budget_are_omitted_without_charging_them(
    harness: Harness,
) -> None:
    search, model = install(harness)
    model.reference_tokens = 2_000
    await harness.grant(limit=1_000)
    request = await harness.submit(harness.member, content="최신 문서를 검색해줘")
    await harness.execute_next()
    row = await search_record(harness, request["id"])
    assert row.status == "omitted" and row.reason == "context_limit" and row.sources == []
    assert len(search.search_calls) == 1 and len(model.calls) == 1
    messages, options = model.calls[0]
    assert not any(message.content.startswith(SEARCH_CONTEXT_PROMPT) for message in messages)
    complete = await snapshot(harness.database, request["id"])
    assert complete.run.status == "completed"
    assert complete.reservation.input_tokens == await model.count_input(messages, options)
    current = await balance(harness.database, harness.member)
    assert current.used_tokens < 1_000
    assert current.used_tokens == (
        complete.reservation.input_tokens + complete.reservation.output_tokens
    )


@pytest.mark.parametrize("error_type", [RuntimeError, ProviderUnavailable])
async def test_search_context_failure_closes_attempt_and_releases_before_model_start(
    harness: Harness, error_type: type[Exception]
) -> None:
    search, model = install(harness)
    model.reference_error = error_type("합성 검색 문맥 준비 오류")
    await harness.grant()
    request = await harness.submit(harness.member, content="최신 문서를 검색해줘")
    await harness.execute_next()
    failed = await snapshot(harness.database, request["id"])
    row = await search_record(harness, request["id"])
    assert len(search.search_calls) == 1 and search.closed.is_set()
    assert model.calls == []
    assert row.status == "failed" and row.reason == "preparation_failed"
    assert row.completed_at is not None and row.sources == []
    assert failed.run.status == failed.assistant.status == "failed"
    assert failed.assistant.content == ""
    assert failed.run.request_messages == []
    assert failed.reservation.status == "released"
    assert failed.reservation.input_tokens == failed.reservation.output_tokens == 0
    assert any(
        event.kind == "error"
        and event.payload.get("message")
        == "모델을 실행하지 못했습니다. 토큰은 차감되지 않았습니다."
        for event in failed.events
    )
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0

    # 준비 실패가 정산 대기로 남아 다음 정상 질문을 가로막지 않아야 한다.
    model.reference_error = None
    next_request = await harness.submit(harness.member, content="파이썬 리스트를 설명해줘")
    await harness.execute_next()
    assert (await snapshot(harness.database, next_request["id"])).run.status == "completed"
    assert len(model.calls) == 1 and len(search.search_calls) == 1


async def test_regeneration_search_sends_only_question_and_preserves_previous_sources(
    harness: Harness,
) -> None:
    search, model = install(harness)
    private_marker = "private-history-sentinel-21a5"
    old_marker = "old-source-sentinel-6432"
    new_marker = "new-source-sentinel-a723"
    await harness.submit(content=f"내 이름은 검증사용자이고 내부 메모는 {private_marker}야")
    await harness.execute_next()
    search.results = (SearchResult("이전 문서", "https://example.com/old", old_marker),)
    content = "최신 Python 문서를 검색해줘"
    first = await harness.submit(content=content)
    await harness.execute_next()
    first_sources = (await search_record(harness, first["id"])).sources
    assert first_sources[0]["snippet"] == old_marker

    search.results = (SearchResult("새 문서", "https://example.com/new", new_marker),)
    regenerated = await harness.service.regenerate(
        harness.system.user_id,
        UUID(first["id"]),
        options=GenerationOptions(max_tokens=64),
        idempotency_key=uuid4(),
    )
    await harness.execute_next()
    assert search.search_calls == [content, content]
    assert all(
        marker not in query
        for query in search.search_calls
        for marker in (private_marker, old_marker, new_marker)
    )
    assert regenerated["user_message_id"] == first["user_message_id"]
    assert (await search_record(harness, first["id"])).sources == first_sources
    assert (await search_record(harness, regenerated["id"])).sources[0]["snippet"] == new_marker
    final_messages = "\n".join(message.content for message in model.calls[-1][0])
    assert private_marker in final_messages
    assert new_marker in final_messages and old_marker not in final_messages
    assert (await snapshot(harness.database, regenerated["id"])).run.status == "completed"


async def test_worker_recovery_terminates_orphan_search_but_preserves_queued_work(
    harness: Harness,
) -> None:
    search, model = install(harness)
    running = await harness.submit(content="최신 문서를 검색해줘")
    job = await harness.worker.claim()
    assert job is not None and job["id"] == UUID(running["id"])
    queued = await harness.submit(harness.other, content="최신 문서를 검색해줘")
    async with harness.database.session() as session:
        session.add_all(
            [
                WebSearchRun(
                    generation_id=UUID(running["id"]),
                    provider="brave",
                    status="searching",
                    sources=[],
                ),
                WebSearchRun(
                    generation_id=UUID(queued["id"]),
                    provider="brave",
                    status="pending",
                    sources=[],
                ),
            ]
        )
        await session.commit()
    await harness.worker.recover()
    recovered = await search_record(harness, running["id"])
    preserved = await search_record(harness, queued["id"])
    assert recovered.status == "cancelled" and recovered.reason == "worker_interrupted"
    assert recovered.completed_at is not None and recovered.sources == []
    assert preserved.status == "pending" and preserved.completed_at is None
    assert (await snapshot(harness.database, running["id"])).run.status == "failed"
    assert (await snapshot(harness.database, queued["id"])).run.status == "queued"
    assert search.check_calls == 0 and search.search_calls == [] and model.calls == []
