"""실제 PostgreSQL에서 생성 승인·저장·취소와 토큰 정산의 원자성을 검증한다."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from backend.app.config import Settings
from backend.app.context.builder import SYSTEM_PROMPT
from backend.app.db import Database
from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable
from backend.app.llm.providers.mock import MockProvider
from backend.app.models import (
    Conversation,
    GenerationEvent,
    GenerationRun,
    Message,
    TokenBudget,
    TokenReservation,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    Repository,
    create_user_with_workspace,
)
from backend.app.runtime import worker as worker_module
from backend.app.runtime.worker import GenerationWorker
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.generations import GenerationService
from backend.app.services.generations import events as generation_module
from backend.app.services.generations.admission import QueueFull
from backend.app.services.token_quota import QuotaExceeded, TokenQuotaService
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@dataclass(frozen=True)
class Account:
    user_id: UUID
    workspace_id: UUID
    conversation_id: UUID


@dataclass
class Harness:
    database: Database
    settings: Settings
    provider: MockProvider
    service: GenerationService
    worker: GenerationWorker
    system: Account
    member: Account
    other: Account

    async def submit(self, account: Account | None = None, **kwargs) -> dict:
        account = account or self.system
        arguments = {
            "content": "저장되는 한글 질문 🦊",
            "options": GenerationOptions(max_tokens=64),
            "idempotency_key": uuid4(),
            **kwargs,
        }
        return await self.service.submit(account.user_id, account.conversation_id, **arguments)

    async def grant(self, account: Account | None = None, limit: int = 10_000) -> None:
        account = account or self.member
        current = datetime.now(UTC)
        async with self.database.session() as session:
            await TokenQuotaService(session, self.system.user_id).grant_budget(
                user_id=account.user_id,
                grant_key=str(uuid4()),
                starts_at=current - timedelta(days=1),
                ends_at=current + timedelta(days=1),
                token_limit=limit,
            )
            await session.commit()

    async def execute_next(self) -> UUID:
        job = await self.worker.claim()
        assert job is not None
        await self.worker.execute(job)
        return job["id"]


@dataclass(frozen=True)
class Snapshot:
    run: GenerationRun
    user_message: Message
    assistant: Message
    reservation: TokenReservation
    conversation: Conversation
    events: list[GenerationEvent]


async def snapshot(database: Database, run_id: UUID | str) -> Snapshot:
    async with database.session() as session:
        run = await session.get(GenerationRun, UUID(str(run_id)))
        result = Snapshot(
            run=run,
            user_message=await session.get(Message, run.user_message_id),
            assistant=await session.get(Message, run.assistant_message_id),
            reservation=await session.get(TokenReservation, run.reservation_id),
            conversation=await session.get(Conversation, run.conversation_id),
            events=list(
                (
                    await session.scalars(
                        select(GenerationEvent)
                        .where(GenerationEvent.generation_id == run.id)
                        .order_by(GenerationEvent.sequence)
                    )
                ).all()
            ),
        )
        # 읽은 스냅샷을 분리해 세션 종료의 rollback으로 속성이 만료되지 않게 한다.
        session.expunge_all()
        return result


async def table_counts(database: Database) -> tuple[int, ...]:
    async with database.session() as session:
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(model))
                for model in (GenerationRun, Message, TokenReservation, GenerationEvent)
            ]
        )


async def balance(database: Database, account: Account):
    async with database.session() as session:
        return await TokenQuotaService(session, account.user_id).get_balance()


@pytest.fixture
async def harness(schema_database: Database, postgres: IsolatedPostgres) -> AsyncIterator[Harness]:
    configured = database_settings(postgres).model_copy(update={"generation_queue_limit": 2})
    accounts = []
    async with schema_database.session() as session:
        for name in ("system", "member", "other"):
            created = await create_user_with_workspace(
                session, email=f"{name}@example.com", display_name=f"테스트 {name}"
            )
            if name == "system":
                created.user.platform_role = "system"
            conversation = await Repository(session, created.user.id).create_conversation(
                created.workspace.id, model=configured.llm_model_id
            )
            accounts.append(Account(created.user.id, created.workspace.id, conversation.id))
        await session.commit()
    provider = MockProvider(configured, delay_seconds=0)
    service = GenerationService(schema_database, provider, configured)
    worker = GenerationWorker(service)
    yield Harness(schema_database, configured, provider, service, worker, *accounts)
    assert schema_database.engine.pool.checkedout() == 0


@pytest.mark.parametrize("account_name", ["member", "system"])
async def test_completed_generation_saves_messages_and_settles_actual_usage(
    harness: Harness, account_name: str
) -> None:
    account = getattr(harness, account_name)
    if account_name == "member":
        await harness.grant(account)
    request = await harness.submit(account)
    pending = await snapshot(harness.database, request["id"])
    assert pending.run.status == "queued"
    assert pending.user_message.role == "user"
    assert pending.user_message.created_by == account.user_id
    assert pending.assistant.role == "assistant"
    assert pending.assistant.status == "pending"
    assert pending.assistant.content == ""
    assert pending.reservation.reserved_tokens == pending.run.prompt_tokens + 64
    assert pending.reservation.charge_mode == "deferred"
    before_generation = await balance(harness.database, account)
    assert before_generation.used_tokens == before_generation.reserved_tokens == 0
    assert before_generation.remaining_tokens == (None if account_name == "system" else 10_000)
    assert pending.reservation.quota_exempt is (account_name == "system")
    assert pending.run.request_messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "저장되는 한글 질문 🦊"},
    ]

    assert await harness.execute_next() == UUID(request["id"])
    complete = await snapshot(harness.database, request["id"])
    assert complete.run.status == complete.assistant.status == "completed"
    assert complete.run.request_messages == []
    assert complete.run.completed_at is not None
    assert complete.run.started_at is not None
    assert "저장되는 한글 질문 🦊" in complete.assistant.content
    assert complete.assistant.token_count == len(complete.assistant.content.split())
    assert complete.reservation.status == "settled"
    assert complete.reservation.input_tokens == pending.run.prompt_tokens
    assert complete.reservation.output_tokens == complete.assistant.token_count
    assert complete.conversation.title == "저장되는 한글 질문 🦊"
    assert complete.conversation.next_message_sequence == 3
    assert [complete.user_message.sequence, complete.assistant.sequence] == [1, 2]
    assert [entry.sequence for entry in complete.events] == list(range(1, len(complete.events) + 1))
    assert complete.run.last_event_sequence == len(complete.events)
    assert sum(entry.kind == "done" for entry in complete.events) == 1
    assert (
        "".join(entry.payload["text"] for entry in complete.events if entry.kind == "delta")
        == complete.assistant.content
    )
    current = await balance(harness.database, account)
    assert current.reserved_tokens == 0
    assert (
        current.used_tokens
        == complete.reservation.input_tokens + complete.reservation.output_tokens
    )
    assert current.unlimited is (account_name == "system")
    if account_name == "system":
        assert current.remaining_tokens is None
        assert complete.reservation.budget_id is None
    else:
        assert current.remaining_tokens == 10_000 - current.used_tokens

    await harness.service.finish(
        complete.run.id,
        input_tokens=complete.reservation.input_tokens,
        output_tokens=complete.reservation.output_tokens,
    )
    repeated = await snapshot(harness.database, request["id"])
    assert repeated.run.last_event_sequence == complete.run.last_event_sequence
    assert (await balance(harness.database, account)).used_tokens == current.used_tokens


@pytest.mark.parametrize("limit", [0, 1])
async def test_exhausted_or_insufficient_plan_does_not_fall_back_to_free_budget(
    harness: Harness, limit: int
) -> None:
    await harness.grant(limit=limit)
    with pytest.raises(QuotaExceeded):
        await harness.submit(harness.member)
    assert await table_counts(harness.database) == (0, 0, 0, 0)
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, harness.member.conversation_id)
        assert conversation.next_message_sequence == 1
        assert conversation.title == "새 대화"


async def test_existing_member_gets_monthly_free_budget_when_submitting(harness: Harness) -> None:
    accepted = await harness.submit(harness.member)
    pending = await balance(harness.database, harness.member)
    assert pending.token_limit == 20_000
    assert pending.budget_source == "free_monthly"
    assert pending.used_tokens == pending.reserved_tokens == 0
    assert pending.remaining_tokens == 20_000
    assert str(await harness.execute_next()) == accepted["id"]
    finished = await balance(harness.database, harness.member)
    assert finished.budget_id == pending.budget_id
    assert finished.used_tokens > 0
    assert finished.reserved_tokens == 0
    assert finished.remaining_tokens == 20_000 - finished.used_tokens


async def test_concurrent_identical_keys_create_one_pair_and_changed_payload_conflicts(
    harness: Harness,
) -> None:
    class RendezvousProvider(MockProvider):
        arrived = 0

        async def count_input(self, messages, options):
            self.arrived += 1
            if self.arrived == 2:
                both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=5)
            return await super().count_input(messages, options)

    both_ready = asyncio.Event()
    provider = RendezvousProvider(harness.settings, delay_seconds=0)
    second_database = Database(harness.settings)
    left = GenerationService(harness.database, provider, harness.settings)
    right = GenerationService(second_database, provider, harness.settings)
    arguments = dict(
        content="동시에 보낸 질문",
        options=GenerationOptions(max_tokens=32),
        idempotency_key=uuid4(),
    )
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                left.submit(harness.system.user_id, harness.system.conversation_id, **arguments),
                right.submit(harness.system.user_id, harness.system.conversation_id, **arguments),
            ),
            timeout=10,
        )
    finally:
        await second_database.dispose()
    assert provider.arrived == 2
    assert results[0]["id"] == results[1]["id"]
    assert await table_counts(harness.database) == (1, 2, 1, 1)
    for changed in ({"content": "변경된 질문"}, {"options": GenerationOptions(max_tokens=33)}):
        with pytest.raises(Conflict):
            await harness.service.submit(
                harness.system.user_id, harness.system.conversation_id, **(arguments | changed)
            )
    await harness.execute_next()
    repeated = await harness.service.submit(
        harness.system.user_id, harness.system.conversation_id, **arguments
    )
    assert repeated["id"] == results[0]["id"]
    assert repeated["status"] == "completed"
    assert (await table_counts(harness.database))[:3] == (1, 2, 1)


async def test_one_active_generation_per_user_applies_across_conversations(
    harness: Harness,
) -> None:
    await harness.submit()
    async with harness.database.session() as session:
        another = await Repository(session, harness.system.user_id).create_conversation(
            harness.system.workspace_id, model=harness.settings.llm_model_id
        )
        await session.commit()
        another_id = another.id
    with pytest.raises(QueueFull):
        await harness.service.submit(
            harness.system.user_id,
            another_id,
            content="다른 대화의 중복 실행",
            options=GenerationOptions(),
            idempotency_key=uuid4(),
        )
    assert (await table_counts(harness.database))[:3] == (1, 2, 1)
    await harness.execute_next()
    accepted = await harness.service.submit(
        harness.system.user_id,
        another_id,
        content="완료 뒤 다시 시작",
        options=GenerationOptions(),
        idempotency_key=uuid4(),
    )
    assert accepted["status"] == "queued"


async def test_global_queue_bound_rejects_atomically_and_cancel_frees_capacity(
    harness: Harness,
) -> None:
    harness.settings.generation_queue_limit = 1
    await harness.grant(harness.member)
    await harness.grant(harness.other)
    running = await harness.submit()
    assert (await harness.worker.claim())["id"] == UUID(running["id"])
    queued = await harness.submit(harness.member)
    with pytest.raises(QueueFull):
        await harness.submit(harness.other)
    assert (await table_counts(harness.database))[:3] == (2, 4, 2)
    assert (await balance(harness.database, harness.other)).reserved_tokens == 0
    await harness.service.cancel(harness.member.user_id, UUID(queued["id"]))
    assert (await harness.submit(harness.other))["status"] == "queued"


async def test_queued_cancel_refunds_and_is_idempotent(harness: Harness) -> None:
    await harness.grant()
    submitted = await harness.submit(harness.member)
    assert (await balance(harness.database, harness.member)).reserved_tokens == 0
    assert (await balance(harness.database, harness.member)).remaining_tokens == 10_000
    result = await harness.service.cancel(harness.member.user_id, UUID(submitted["id"]))
    assert result["status"] == "cancelled"
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.assistant.status == saved.run.status == "cancelled"
    assert saved.run.cancel_requested is True
    assert saved.run.request_messages == []
    assert saved.reservation.status == "released"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    assert saved.events[-1].kind == "cancelled"
    assert await harness.worker.claim() is None
    again = await harness.service.cancel(harness.member.user_id, saved.run.id)
    assert again["last_event_id"] == result["last_event_id"]
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0
    assert current.remaining_tokens == 10_000


@pytest.mark.parametrize("remote_api", [False, True])
async def test_running_cancel_closes_stalled_provider_and_settles_received_tokens(
    harness: Harness, monkeypatch, remote_api: bool
) -> None:
    sent = asyncio.Event()
    closed = asyncio.Event()

    class PausedProvider(MockProvider):
        async def stream(self, messages, options):
            try:
                yield ProviderDelta(text="중단 전 답변. ", received_output_tokens=3)
                sent.set()
                await asyncio.Event().wait()
                pytest.fail("중단 후 다음 토큰을 기다리면 안 됩니다.")
            finally:
                closed.set()

    monotonic_values = count()
    monkeypatch.setattr(worker_module, "monotonic", lambda: next(monotonic_values))
    harness.service.provider = PausedProvider(harness.settings, delay_seconds=0)
    await harness.grant()
    submitted = await harness.submit(harness.member)
    job = await harness.worker.claim()
    execution = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(sent.wait(), timeout=5)
        assert (
            await snapshot(harness.database, submitted["id"])
        ).assistant.content == "중단 전 답변. "
        # 별도 서비스 인스턴스는 메모리 신호를 공유하지 않는 다른 API 프로세스에 해당한다.
        canceller = (
            GenerationService(harness.database, harness.service.provider, harness.settings)
            if remote_api
            else harness.service
        )
        response = await canceller.cancel(harness.member.user_id, UUID(submitted["id"]))
        assert response["status"] == "running"
        assert response["cancel_requested"] is True
        await asyncio.wait_for(execution, timeout=1)
        assert closed.is_set()
        assert not harness.service.cancel_events
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == saved.assistant.status == "cancelled"
    assert saved.assistant.content == "중단 전 답변. "
    assert saved.reservation.status == "settled"
    assert saved.reservation.output_tokens == 3
    assert saved.reservation.usage_basis == "received"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens
    assert all("중단 후" not in str(entry.payload) for entry in saved.events)
    assert saved.events[-1].kind == "cancelled"
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == saved.run.prompt_tokens + 3
    assert current.reserved_tokens == 0
    assert (await harness.submit(harness.member, content="즉시 다음 질문"))["status"] == "queued"


@pytest.mark.parametrize("emit_unverified_text", [False, True])
async def test_running_cancel_waives_usage_when_no_generated_tokens_are_confirmed(
    harness: Harness, emit_unverified_text: bool
) -> None:
    started, closed = asyncio.Event(), asyncio.Event()

    class StalledProvider(MockProvider):
        async def stream(self, messages, options):
            try:
                if emit_unverified_text:
                    yield ProviderDelta(text="수신 토큰 수가 없는 제공자 응답")
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    harness.service.provider = StalledProvider(harness.settings, delay_seconds=0)
    await harness.grant()
    submitted = await harness.submit(harness.member)
    job = await harness.worker.claim()
    execution = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await harness.service.cancel(harness.member.user_id, job["id"])
        await asyncio.wait_for(execution, timeout=1)
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)
    assert closed.is_set()
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "cancelled"
    assert saved.reservation.status == "settled"
    assert saved.reservation.usage_basis == "waived"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    assert saved.events[-1].payload["usage_basis"] == "waived"
    current = await balance(harness.database, harness.member)
    assert current.reserved_tokens == current.used_tokens == 0


@pytest.mark.parametrize("received", [0, 3])
async def test_committed_cancellation_wins_over_failure_before_worker_notices_cancel(
    harness: Harness, received: int
) -> None:
    await harness.grant()
    submitted = await harness.submit(harness.member)
    job = await harness.worker.claim()
    await harness.service.cancel(harness.member.user_id, job["id"])
    # 중단 감지 작업이 완료되기 전에 전송 오류 처리로 진입한 경우를 재현한다.
    await harness.service.finish(
        job["id"], error_code="provider_unavailable", received_output_tokens=received
    )
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "cancelled"
    assert saved.reservation.usage_basis == ("received" if received else "waived")
    assert saved.reservation.output_tokens == received
    assert saved.reservation.input_tokens == (saved.run.prompt_tokens if received else 0)
    assert (await balance(harness.database, harness.member)).reserved_tokens == 0


@pytest.mark.parametrize("started", [False, True])
async def test_provider_failure_without_final_usage_waives_charge_and_preserves_partial_answer(
    harness: Harness, started: bool
) -> None:
    class BrokenProvider(MockProvider):
        async def stream(self, messages, options):
            if started:
                yield ProviderDelta(text="실패 전에 생성된 부분 답변")
            raise ProviderUnavailable("안전한 테스트 오류", request_started=started)

    harness.service.provider = BrokenProvider(harness.settings, delay_seconds=0)
    await harness.grant()
    submitted = await harness.submit(harness.member)
    await harness.execute_next()
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "failed"
    assert saved.reservation.status == "released"
    assert saved.reservation.usage_basis == "waived"
    assert saved.assistant.token_count is None
    assert saved.assistant.status == "failed"
    assert saved.assistant.content == ("실패 전에 생성된 부분 답변" if started else "")
    assert saved.run.request_messages == []
    assert saved.events[-1].kind == "error"
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == 0
    assert current.reserved_tokens == 0
    assert current.remaining_tokens == 10_000
    assert (await harness.submit(harness.member, content="실패 후 다시 질문"))["status"] == "queued"


async def test_stream_without_terminal_usage_ends_without_charging(harness: Harness) -> None:
    class NoUsageProvider(MockProvider):
        async def stream(self, messages, options):
            yield ProviderDelta(text="보이지만 사용량은 없는 답변")

    harness.service.provider = NoUsageProvider(harness.settings, delay_seconds=0)
    submitted = await harness.submit()
    await harness.execute_next()
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "failed"
    assert saved.reservation.status == "released"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    assert saved.reservation.usage_basis == "waived"
    assert saved.assistant.token_count is None
    assert saved.assistant.content == "보이지만 사용량은 없는 답변"


@pytest.mark.parametrize("mismatch", ["input", "output", "negative", "boolean"])
async def test_untrustworthy_usage_never_charges_or_blocks_next_request(
    harness: Harness, mismatch: str
) -> None:
    submitted = await harness.submit()
    job = await harness.worker.claim()
    await harness.service.append_chunk(job["id"], "생성된 내용")
    before = await snapshot(harness.database, submitted["id"])
    input_tokens, output_tokens = before.run.prompt_tokens, 3
    if mismatch == "input":
        input_tokens += 1
    elif mismatch == "output":
        output_tokens = before.run.max_output_tokens + 1
    elif mismatch == "negative":
        output_tokens = -1
    else:
        output_tokens = True
    await harness.service.finish(job["id"], input_tokens=input_tokens, output_tokens=output_tokens)
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "failed"
    assert saved.reservation.status == "released"
    assert saved.reservation.input_tokens == saved.reservation.output_tokens == 0
    assert (await balance(harness.database, harness.system)).used_tokens == 0


async def test_worker_recovery_waives_interrupted_usage_and_leaves_queued_jobs_untouched(
    harness: Harness,
) -> None:
    await harness.grant()
    first = await harness.submit()
    await harness.worker.claim()
    queued = await harness.submit(harness.member)
    await harness.worker.recover()
    interrupted = await snapshot(harness.database, first["id"])
    pending = await snapshot(harness.database, queued["id"])
    assert interrupted.run.status == "failed"
    assert interrupted.run.error_code == "worker_interrupted"
    assert interrupted.reservation.status == "released"
    assert interrupted.run.request_messages == []
    assert pending.run.status == "queued"
    assert pending.run.started_at is None
    assert pending.run.request_messages
    assert pending.reservation.status == "reserved"
    await harness.worker.recover()
    assert (
        await snapshot(harness.database, first["id"])
    ).run.last_event_sequence == interrupted.run.last_event_sequence
    assert await harness.execute_next() == UUID(queued["id"])
    assert (await snapshot(harness.database, queued["id"])).run.status == "completed"


async def create_old_pending_request(
    harness: Harness, account: Account, *, legacy: bool = False
) -> UUID:
    """이전 코드가 남긴 종료된 미정산 요청을 실제 저장된 상태로 재현한다."""
    submitted = await harness.submit(account)
    job = await harness.worker.claim()
    await harness.service.append_chunk(job["id"], "실패 전에 저장된 답변")
    async with harness.database.session() as session:
        run = await session.get(GenerationRun, UUID(submitted["id"]))
        run.status, run.error_code = "usage_pending", "worker_stopped"
        run.completed_at = datetime.now(UTC) - timedelta(minutes=20)
        run.request_messages = []
        assistant = await session.get(Message, run.assistant_message_id)
        assistant.status = "failed"
        if legacy:
            reservation = await session.get(TokenReservation, run.reservation_id)
            reservation.charge_mode = "reserved"
            budget = await session.get(TokenBudget, reservation.budget_id)
            budget.reserved_tokens += reservation.reserved_tokens
        generation_module.add_event(session, run, "error", {"message": "이전 사용량 확인 보류"})
        await session.commit()
    return job["id"]


@pytest.mark.parametrize("entry", ["submit", "worker", "concurrent"])
async def test_old_pending_deferred_recovers_once_without_losing_answer_or_end_time(
    harness: Harness, entry: str
) -> None:
    await harness.grant()
    run_id = await create_old_pending_request(harness, harness.member)
    before = await snapshot(harness.database, run_id)
    if entry == "submit":
        followup = await harness.submit(harness.member, content="새 질문")
        assert followup["status"] == "queued"
    elif entry == "worker":
        await harness.worker.recover()
    else:
        await asyncio.gather(
            harness.service.recover_unsettled(harness.member.user_id),
            harness.service.recover_unsettled(harness.member.user_id),
        )
    after = await snapshot(harness.database, run_id)
    assert after.run.status == after.assistant.status == "failed"
    assert after.run.error_code == "worker_stopped"
    assert after.run.completed_at == before.run.completed_at
    assert after.assistant.content == before.assistant.content
    assert after.assistant.token_count is None
    assert after.reservation.status == "released"
    assert after.reservation.usage_basis == "waived"
    assert after.reservation.input_tokens == after.reservation.output_tokens == 0
    assert after.events[-1].payload["charged_tokens"] == 0
    assert len(after.events) == len(before.events) + 1
    await harness.service.recover_unsettled(harness.member.user_id)
    repeated = await snapshot(harness.database, run_id)
    assert repeated.run.last_event_sequence == after.run.last_event_sequence
    current = await balance(harness.database, harness.member)
    assert current.used_tokens == current.reserved_tokens == 0
    assert current.remaining_tokens == 10_000


async def test_account_recovery_ignores_other_users_and_legacy_reserved_usage(
    harness: Harness,
) -> None:
    await harness.grant(harness.member)
    await harness.grant(harness.other)
    mine = await create_old_pending_request(harness, harness.member)
    other = await create_old_pending_request(harness, harness.other)
    await harness.service.recover_unsettled(harness.member.user_id)
    assert (await snapshot(harness.database, mine)).run.status == "failed"
    assert (await snapshot(harness.database, other)).run.status == "usage_pending"
    legacy = await create_old_pending_request(harness, harness.member, legacy=True)
    before = await balance(harness.database, harness.member)
    await harness.worker.recover()
    saved = await snapshot(harness.database, legacy)
    assert saved.run.status == "usage_pending"
    assert saved.reservation.status == "reserved"
    assert saved.reservation.charge_mode == "reserved"
    after = await balance(harness.database, harness.member)
    assert after.used_tokens == before.used_tokens == 0
    assert after.reserved_tokens == before.reserved_tokens > 0


@pytest.mark.parametrize("change", ["disabled_user", "removed_membership", "archived_workspace"])
async def test_worker_rechecks_access_after_queue_admission_and_refunds_without_calling_model(
    harness: Harness, change: str
) -> None:
    class ObservedProvider(MockProvider):
        calls = 0

        async def stream(self, messages, options):
            self.calls += 1
            async for delta in super().stream(messages, options):
                yield delta

    provider = ObservedProvider(harness.settings, delay_seconds=0)
    harness.service.provider = provider
    submitted = await harness.submit()
    async with harness.database.session() as session:
        if change == "disabled_user":
            await session.execute(
                update(User).where(User.id == harness.system.user_id).values(status="disabled")
            )
        elif change == "removed_membership":
            await session.execute(
                delete(WorkspaceMember).where(WorkspaceMember.user_id == harness.system.user_id)
            )
        else:
            await session.execute(
                update(Workspace)
                .where(Workspace.id == harness.system.workspace_id)
                .values(status="archived")
            )
        await session.commit()
    await harness.execute_next()
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.run.status == "failed"
    assert saved.run.error_code == "access_revoked"
    assert saved.reservation.status == "released"
    assert provider.calls == 0


async def test_foreign_conversations_and_cancellation_are_denied_even_for_system_accounts(
    harness: Harness,
) -> None:
    await harness.grant()
    request = await harness.submit(harness.member)
    for actor in (harness.system, harness.other):
        with pytest.raises(AccessDenied):
            await harness.service.submit(
                actor.user_id,
                harness.member.conversation_id,
                content="남의 대화에 주입할 메시지",
                options=GenerationOptions(),
                idempotency_key=uuid4(),
            )
        with pytest.raises(AccessDenied):
            await harness.service.cancel(actor.user_id, UUID(request["id"]))
    assert (await table_counts(harness.database))[:3] == (1, 2, 1)
    assert (await snapshot(harness.database, request["id"])).run.status == "queued"


async def test_context_keeps_cancelled_user_and_excludes_unlinked_messages(
    harness: Harness,
) -> None:
    first = await harness.submit(content="첫 번째 저장 질문")
    await harness.execute_next()
    completed = await snapshot(harness.database, first["id"])
    cancelled = await harness.submit(content="중단되어도 기억할 질문")
    await harness.service.cancel(harness.system.user_id, UUID(cancelled["id"]))
    async with harness.database.session() as session:
        await Repository(session, harness.system.user_id).append_message(
            harness.system.workspace_id,
            harness.system.conversation_id,
            role="assistant",
            content="완료된 생성에 속하지 않는 메시지",
        )
        await session.commit()
    followup = await harness.submit(content="새 질문")
    pending = await snapshot(harness.database, followup["id"])
    assert pending.run.request_messages == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "첫 번째 저장 질문"},
        {"role": "assistant", "content": completed.assistant.content},
        {"role": "user", "content": "중단되어도 기억할 질문"},
        {"role": "user", "content": "새 질문"},
    ]
    context = [ChatMessage.model_validate(message) for message in pending.run.request_messages]
    assert pending.run.prompt_tokens == await harness.provider.count_input(
        context, GenerationOptions(max_tokens=64)
    )
    await harness.execute_next()
    assert (await snapshot(harness.database, followup["id"])).run.status == "completed"


@pytest.mark.parametrize("failure", ["unavailable", "context_limit", "boolean"])
async def test_count_preflight_failure_never_creates_messages_or_reservation(
    harness: Harness, failure: str
) -> None:
    class RejectedCountProvider(MockProvider):
        async def count_input(self, messages, options):
            if failure == "unavailable":
                raise ProviderUnavailable("토큰 계산 불가", request_started=False)
            if failure == "context_limit":
                return harness.settings.llm_context_window
            return True

    harness.service.provider = RejectedCountProvider(harness.settings, delay_seconds=0)
    expected = InvalidInput if failure == "context_limit" else ProviderUnavailable
    with pytest.raises(expected):
        await harness.submit()
    assert await table_counts(harness.database) == (0, 0, 0, 0)


async def test_retrying_a_committed_chunk_at_the_same_offset_does_not_duplicate_text_or_events(
    harness: Harness,
) -> None:
    submitted = await harness.submit()
    job = await harness.worker.claim()
    first, second = "첫 청크 ", "다음 청크"
    assert await harness.service.append_chunk(job["id"], first, offset=0) is False
    committed = await snapshot(harness.database, submitted["id"])
    assert await harness.service.append_chunk(job["id"], first, offset=0) is False
    repeated = await snapshot(harness.database, submitted["id"])
    assert repeated.assistant.content == first
    assert repeated.run.last_event_sequence == committed.run.last_event_sequence
    assert [entry.payload["text"] for entry in repeated.events if entry.kind == "delta"] == [first]

    await harness.service.append_chunk(job["id"], second, offset=len(first))
    await harness.service.append_chunk(job["id"], second, offset=len(first))
    with pytest.raises(Conflict):
        await harness.service.append_chunk(job["id"], "다른 본문", offset=len(first))
    saved = await snapshot(harness.database, submitted["id"])
    assert saved.assistant.content == first + second
    assert saved.run.last_event_sequence == committed.run.last_event_sequence + 1
    assert [entry.payload["text"] for entry in saved.events if entry.kind == "delta"] == [
        first,
        second,
    ]


async def test_worker_cancel_after_final_usage_and_chunk_commit_preserves_text_and_settles(
    harness: Harness,
    monkeypatch,
) -> None:
    await harness.grant()
    submitted = await harness.submit(harness.member)
    job = await harness.worker.claim()
    committed = asyncio.Event()
    gate = asyncio.Event()
    append_chunk = harness.service.append_chunk
    calls = 0

    async def pause_after_first_commit(run_id, chunk, *, offset=None):
        nonlocal calls
        calls += 1
        result = await append_chunk(run_id, chunk, offset=offset)
        if calls == 1:
            # usage를 받은 뒤 청크 커밋은 성공했지만 실행자의 offset 갱신 전인 경계다.
            committed.set()
            await gate.wait()
        return result

    # 짧은 답변의 시간 기준 flush를 막아 첫 저장이 final usage 이후에 일어나게 한다.
    monkeypatch.setattr(worker_module, "monotonic", lambda: 0)
    monkeypatch.setattr(harness.service, "append_chunk", pause_after_first_commit)
    execution = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(committed.wait(), timeout=5)
        before_cancel = await snapshot(harness.database, submitted["id"])
        assert before_cancel.assistant.content
        assert before_cancel.reservation.status == "reserved"
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=5)
    finally:
        gate.set()
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)

    saved = await snapshot(harness.database, submitted["id"])
    assert calls == 2
    assert saved.run.status == saved.assistant.status == "failed"
    assert saved.run.error_code == "worker_stopped"
    assert saved.run.request_messages == []
    assert saved.assistant.content == before_cancel.assistant.content
    assert [entry.payload["text"] for entry in saved.events if entry.kind == "delta"] == [
        saved.assistant.content
    ]
    assert saved.reservation.status == "settled"
    assert saved.reservation.input_tokens == saved.run.prompt_tokens
    assert saved.reservation.output_tokens == len(saved.assistant.content.split())
    assert saved.assistant.token_count == saved.reservation.output_tokens
    current = await balance(harness.database, harness.member)
    assert current.reserved_tokens == 0
    assert current.used_tokens == saved.reservation.input_tokens + saved.reservation.output_tokens


async def test_length_limited_answer_is_kept_for_continue_request(harness: Harness) -> None:
    first = await harness.submit(
        content="긴 예제를 보여 줘", options=GenerationOptions(max_tokens=1)
    )
    await harness.execute_next()
    saved = await snapshot(harness.database, first["id"])
    assert saved.run.status == "completed"
    assert (
        next(event for event in saved.events if event.kind == "done").payload["finish_reason"]
        == "length"
    )
    followup = await harness.submit(content="이어서 말해")
    pending = await snapshot(harness.database, followup["id"])
    assert pending.run.request_messages[-2:] == [
        {"role": "assistant", "content": saved.assistant.content},
        {"role": "user", "content": "이어서 말해"},
    ]
    assert "출력 토큰 한도" in pending.run.request_messages[0]["content"]
    assert pending.run.max_output_tokens == 64
    await harness.execute_next()
    assert (await snapshot(harness.database, followup["id"])).run.status == "completed"


async def test_default_output_limit_fits_remaining_context(harness: Harness) -> None:
    class NearlyFullProvider(MockProvider):
        async def count_input(self, messages, options):
            return harness.settings.llm_context_window - 700

    harness.service.provider = NearlyFullProvider(harness.settings, delay_seconds=0)
    options = GenerationOptions(thinking=True)
    assert options.max_tokens == 4_096
    key = uuid4()
    submitted = await harness.submit(options=options, idempotency_key=key)
    pending = await snapshot(harness.database, submitted["id"])
    assert pending.run.max_output_tokens == 700
    assert pending.run.options["max_tokens"] == 700
    assert (
        pending.run.prompt_tokens + pending.run.max_output_tokens
        == harness.settings.llm_context_window
    )
    retried = await harness.submit(options=options, idempotency_key=key)
    assert retried["id"] == submitted["id"]
