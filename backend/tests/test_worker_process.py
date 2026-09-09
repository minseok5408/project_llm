"""격리 PostgreSQL과 mock 자식 프로세스로 API 재시작·프로세스 종료를 검증한다."""

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from backend.app.db import Database
from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app
from backend.app.models import GenerationRun, TokenReservation, WorkerHeartbeat
from backend.app.repositories import Repository, create_user_with_workspace
from backend.app.schemas import GenerationOptions
from backend.app.services.generations import GenerationService
from backend.tests.conftest import PROJECT_ROOT, IsolatedPostgres, database_settings

pytestmark = pytest.mark.postgres

# 모델 서버나 웹 서버를 열지 않고 실제 worker 진입점에 파일로 제어하는 mock만 주입한다.
PROCESS_CODE = """
import asyncio
import os
from pathlib import Path
from backend.app import worker
from backend.app.config import Settings
from backend.app.llm.providers.mock import MockProvider

gate = Path(os.environ["WORKER_TEST_GATE"])
settings = Settings(
    _env_file=None,
    database_enabled=True,
    database_url=os.environ["WORKER_TEST_DATABASE_URL"],
    llm_backend="mock",
)

class GatedProvider(MockProvider):
    async def stream(self, messages, options):
        with gate.with_suffix(".calls").open("a") as output:
            output.write("call\\n")
        gate.with_suffix(".entered").touch()
        try:
            while not gate.exists():
                await asyncio.sleep(0.01)
            async for delta in super().stream(messages, options):
                yield delta
        finally:
            gate.with_suffix(".closed").touch()

worker.build_provider = lambda config: GatedProvider(config, delay_seconds=0)
asyncio.run(worker.run_worker(settings))
"""


async def start_process(postgres: IsolatedPostgres, gate: Path):
    environment = {
        **os.environ,
        "WORKER_TEST_DATABASE_URL": postgres.url,
        "WORKER_TEST_GATE": str(gate),
    }
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        PROCESS_CODE,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


async def stop_process(process):
    if process is not None and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise AssertionError("worker가 SIGTERM 뒤 정상 종료하지 못했습니다.") from None


async def wait_file(path: Path, process):
    async with asyncio.timeout(8):
        while not await asyncio.to_thread(path.exists):
            assert process.returncode is None
            await asyncio.sleep(0.02)


async def wait_status(database: Database, run_id: UUID, expected: str):
    async with asyncio.timeout(8):
        while True:
            async with database.session() as session:
                run = await session.get(GenerationRun, run_id)
                if run.status == expected:
                    session.expunge(run)
                    return run
                assert run.status in ("queued", "running")
            await asyncio.sleep(0.02)


async def account_and_conversation(database: Database, settings):
    async with database.session() as session:
        account = await create_user_with_workspace(
            session, email="independent-worker@example.com", display_name="독립 실행자 검증"
        )
        conversation = await Repository(session, account.user.id).create_conversation(
            account.workspace.id, model=settings.llm_model_id
        )
        result = account.user.id, conversation.id
        await session.commit()
        return result


async def submit(service: GenerationService, ids):
    result = await service.submit(
        *ids,
        content="별도 프로세스로 생성하는 테스트 질문",
        options=GenerationOptions(max_tokens=64),
        idempotency_key=uuid4(),
    )
    return UUID(result["id"])


async def test_worker_process_survives_api_restart_and_obeys_database_cancel(
    schema_database: Database, postgres: IsolatedPostgres, tmp_path: Path
):
    settings = database_settings(postgres)
    ids = await account_and_conversation(schema_database, settings)
    gate = tmp_path / "release"
    process = None
    try:
        app = create_app(settings=settings)
        async with app.router.lifespan_context(app):
            run_id = await submit(app.state.generations, ids)
            process = await start_process(postgres, gate)
            await wait_file(gate.with_suffix(".entered"), process)
            assert process.pid != os.getpid()

        # API의 DB 풀을 닫고 새 lifespan을 열어도 별도 프로세스의 모델 연결은 살아 있다.
        new_app = create_app(settings=settings)
        async with (
            new_app.router.lifespan_context(new_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(new_app), base_url="http://test"
            ) as client,
        ):
            await asyncio.sleep(0.15)
            await wait_status(schema_database, run_id, "running")
            assert not gate.with_suffix(".closed").exists()
            response = await client.get("/health/worker")
            assert response.status_code == 200
            assert response.json() == {
                "status": "ready",
                "state": "busy",
                "queued": 0,
                "running": 1,
            }
            assert response.headers["cache-control"] == "no-store"
            await new_app.state.generations.cancel(ids[0], run_id)
            await wait_status(schema_database, run_id, "cancelled")
            await wait_file(gate.with_suffix(".closed"), process)
            async with schema_database.session() as session:
                run = await session.get(GenerationRun, run_id)
                reservation = await session.get(TokenReservation, run.reservation_id)
                assert reservation.usage_basis == "waived"
                assert reservation.input_tokens == reservation.output_tokens == 0

            gate.touch()
            second_run = await submit(new_app.state.generations, ids)
            await wait_status(schema_database, second_run, "completed")
            await stop_process(process)
            assert process.returncode == 0
            response = await client.get("/health/worker")
            assert response.status_code == 503
            assert response.json()["state"] == "offline"

        async with schema_database.session() as session:
            heartbeat = await session.get(WorkerHeartbeat, "generation")
            assert heartbeat.stopped_at is not None
            assert heartbeat.generation_id is None
        assert gate.with_suffix(".calls").read_text().splitlines() == ["call", "call"]
    finally:
        await stop_process(process)


async def test_worker_process_recovers_after_lock_owner_is_killed_without_replaying_job(
    schema_database: Database, postgres: IsolatedPostgres, tmp_path: Path
):
    settings = database_settings(postgres)
    ids = await account_and_conversation(schema_database, settings)
    service = GenerationService(schema_database, MockProvider(settings), settings)
    run_id = await submit(service, ids)
    gate = tmp_path / "release"
    processes = []
    try:
        first = await start_process(postgres, gate)
        processes.append(first)
        await wait_file(gate.with_suffix(".entered"), first)
        first.kill()
        await first.wait()
        await wait_status(schema_database, run_id, "running")
        second = await start_process(postgres, gate)
        processes.append(second)
        recovered = await wait_status(schema_database, run_id, "failed")
        assert recovered.error_code == "worker_interrupted"
        async with schema_database.session() as session:
            reservation = await session.get(TokenReservation, recovered.reservation_id)
            assert reservation.status == "released"
            assert reservation.usage_basis == "waived"
            assert reservation.input_tokens == reservation.output_tokens == 0

        gate.touch()
        next_run = await submit(service, ids)
        await wait_status(schema_database, next_run, "completed")
        assert gate.with_suffix(".calls").read_text().splitlines() == ["call", "call"]
    finally:
        for process in processes:
            await stop_process(process)


async def test_stale_heartbeat_is_read_only_and_never_replays_a_running_job(
    schema_database: Database, postgres: IsolatedPostgres
):
    settings = database_settings(postgres)
    ids = await account_and_conversation(schema_database, settings)
    service = GenerationService(schema_database, MockProvider(settings), settings)
    run_id = await submit(service, ids)
    async with schema_database.session() as session:
        run = await session.get(GenerationRun, run_id)
        run.status = "running"
        stale_time = run.created_at - timedelta(minutes=1)
        session.add(
            WorkerHeartbeat(
                name="generation",
                worker_id=uuid4(),
                started_at=stale_time,
                heartbeat_at=stale_time,
                generation_id=run_id,
            )
        )
        await session.commit()
    app = create_app(settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        for _ in range(2):
            response = await client.get("/health/worker")
            assert response.status_code == 503
            assert response.json()["running"] == 1
    async with schema_database.session() as session:
        run = await session.get(GenerationRun, run_id)
        assert run.status == "running"
        assert run.completed_at is None
        reservation = await session.get(TokenReservation, run.reservation_id)
        assert reservation.status == "reserved"
