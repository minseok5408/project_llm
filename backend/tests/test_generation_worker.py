"""백그라운드 실행자 두 개의 단일 리더 선출과 DB 연결 정리를 검증한다."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4

import pytest

from backend.app.db import Database
from backend.app.llm.protocol import ProviderDelta
from backend.app.llm.providers.mock import MockProvider
from backend.app.models import GenerationRun, Message, TokenReservation
from backend.app.repositories import Repository, create_user_with_workspace
from backend.app.runtime.worker import GenerationWorker
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.generations import GenerationService
from backend.app.services.token_quota import TokenQuotaService
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = pytest.mark.postgres


class GatedProvider(MockProvider):
    """생성을 잠시 대기시켜 두 실행자가 연결된 동안 리더 잠금을 확인한다."""

    def __init__(self, settings):
        super().__init__(settings, delay_seconds=0)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.stream_calls = 0
        self.closed_streams = 0

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ProviderDelta]:
        self.stream_calls += 1
        self.entered.set()
        try:
            await self.release.wait()
            async for delta in super().stream(messages, options):
                yield delta
        finally:
            self.closed_streams += 1


async def test_two_background_workers_execute_once_and_close_their_leader_connections(
    schema_database: Database, postgres: IsolatedPostgres
):
    settings = database_settings(postgres)
    async with schema_database.session() as session:
        account = await create_user_with_workspace(
            session, email="worker@example.com", display_name="실행자 테스트"
        )
        account.user.platform_role = "system"
        await session.flush()
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model=settings.llm_model_id
        )
        user_id, conversation_id = account.user.id, conversation.id
        await session.commit()
    provider = GatedProvider(settings)
    service = GenerationService(schema_database, provider, settings)
    request = await service.submit(
        user_id,
        conversation_id,
        content="백그라운드에서 한 번만 실행할 질문",
        options=GenerationOptions(max_tokens=64),
        idempotency_key=uuid4(),
    )
    baseline_connections = await postgres.active_connection_count()
    workers = (GenerationWorker(service), GenerationWorker(service))
    for worker in workers:
        worker.start()
    try:
        async with asyncio.timeout(5):
            await provider.entered.wait()
            while True:
                assert all(not worker.task.done() for worker in workers)
                if all(
                    worker.connection is not None and not worker.connection.is_closed()
                    for worker in workers
                ):
                    break
                await asyncio.sleep(0.05)
        pids = [worker.connection.get_server_pid() for worker in workers]
        assert len(set(pids)) == 2
        assert (
            await postgres.admin.fetchval(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND granted AND pid = ANY($1::int[])",
                pids,
            )
            == 1
        )
        assert provider.stream_calls == 1
        async with schema_database.session() as session:
            pending_balance = await TokenQuotaService(session, user_id).get_balance()
            assert pending_balance.unlimited
            assert pending_balance.used_tokens == pending_balance.reserved_tokens == 0
        provider.release.set()
        async with asyncio.timeout(5):
            while True:
                async with schema_database.session() as session:
                    run = await session.get(GenerationRun, UUID(request["id"]))
                    status = run.status
                    if status == "completed":
                        reservation = await session.get(TokenReservation, run.reservation_id)
                        assert reservation.status == "settled"
                        assert reservation.charge_mode == "deferred"
                        assert reservation.usage_basis == "provider"
                        assert reservation.output_tokens > 0
                        balance = await TokenQuotaService(session, user_id).get_balance()
                        assert balance.used_tokens == (
                            reservation.input_tokens + reservation.output_tokens
                        )
                        assert balance.reserved_tokens == 0
                        break
                    assert status in ("queued", "running")
                await asyncio.sleep(0.05)
        assert provider.stream_calls == provider.closed_streams == 1
    finally:
        provider.release.set()
        async with asyncio.timeout(5):
            await asyncio.gather(*(worker.stop() for worker in workers))
    assert all(worker.task.done() and worker.connection is None for worker in workers)
    assert schema_database.engine.pool.checkedout() == 0
    assert await postgres.active_connection_count() <= baseline_connections
    assert (
        await postgres.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE pid = ANY($1::int[])", pids
        )
        == 0
    )
    assert (
        await postgres.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = $1 AND state LIKE 'idle in transaction%'",
            postgres.name,
        )
        == 0
    )


async def test_worker_stop_without_final_usage_does_not_block_member_after_restart(
    schema_database: Database, postgres: IsolatedPostgres
):
    settings = database_settings(postgres)
    async with schema_database.session() as session:
        account = await create_user_with_workspace(
            session, email="interrupted-worker@example.com", display_name="실행자 재시작 테스트"
        )
        assert account.user.platform_role == "member"
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model=settings.llm_model_id
        )
        user_id, conversation_id = account.user.id, conversation.id
        await session.commit()
    provider = GatedProvider(settings)
    service = GenerationService(schema_database, provider, settings)
    request = await service.submit(
        user_id,
        conversation_id,
        content="서버 종료 중 사용량을 전달받지 못하는 질문",
        options=GenerationOptions(max_tokens=64),
        idempotency_key=uuid4(),
    )
    async with schema_database.session() as session:
        original_balance = await TokenQuotaService(session, user_id).get_balance()
    worker = GenerationWorker(service)
    worker.start()
    try:
        async with asyncio.timeout(5):
            await provider.entered.wait()
            await worker.stop()
        assert worker.task.done()
        assert worker.connection is None
        assert provider.stream_calls == provider.closed_streams == 1
        async with schema_database.session() as session:
            interrupted = await session.get(GenerationRun, UUID(request["id"]))
            assert interrupted.status == "failed"
            assert interrupted.error_code == "worker_stopped"
            assert interrupted.request_messages == []
            assert interrupted.completed_at is not None
            assistant = await session.get(Message, interrupted.assistant_message_id)
            assert assistant.status == "failed"
            assert assistant.token_count is None
            reservation = await session.get(TokenReservation, interrupted.reservation_id)
            assert reservation.status == "released"
            assert reservation.charge_mode == "deferred"
            assert reservation.usage_basis == "waived"
            assert reservation.input_tokens == reservation.output_tokens == 0
            assert await TokenQuotaService(session, user_id).get_balance() == original_balance

        # 실행자가 정상 종료되거나 다시 시작되어도 앞선 실패가 다음 승인을 막지 않는다.
        next_request = await service.submit(
            user_id,
            conversation_id,
            content="재시작 뒤 정상적으로 생성할 질문",
            options=GenerationOptions(max_tokens=64),
            idempotency_key=uuid4(),
        )
        assert next_request["status"] == "queued"
        provider.release.set()
        worker.start()
        async with asyncio.timeout(5):
            while True:
                async with schema_database.session() as session:
                    run = await session.get(GenerationRun, UUID(next_request["id"]))
                    if run.status == "completed":
                        reservation = await session.get(TokenReservation, run.reservation_id)
                        assert reservation.status == "settled"
                        assert reservation.usage_basis == "provider"
                        balance = await TokenQuotaService(session, user_id).get_balance()
                        assert balance.used_tokens == (
                            reservation.input_tokens + reservation.output_tokens
                        )
                        assert balance.reserved_tokens == 0
                        break
                    assert run.status in ("queued", "running")
                await asyncio.sleep(0.05)
        assert provider.stream_calls == provider.closed_streams == 2
    finally:
        provider.release.set()
        async with asyncio.timeout(5):
            await worker.stop()
    assert worker.connection is None
    assert schema_database.engine.pool.checkedout() == 0
