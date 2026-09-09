"""실제 PostgreSQL 서버의 격리된 데이터베이스를 사용하는 통합 검사.

``python scripts/test_db.py``로 임시 로컬 서버를 준비하거나, 데이터베이스 생성 권한이
있는 역할로 접속하는 전용 qwen_test 데이터베이스를 TEST_DATABASE_URL에 설정한다.
각 테스트는 임의의 이름으로 독립된 데이터베이스를 생성하고 삭제한다. 전달받은
데이터베이스는 관리 연결에만 사용하며, 여기서 마이그레이션하거나 초기화하지 않는다.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.responses import StreamingResponse
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, Text, func, inspect, select
from sqlalchemy import text as sql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from backend.app.db import Database, DBSession
from backend.app.main import create_app
from backend.app.providers import MockProvider
from backend.tests.conftest import (
    PROJECT_ROOT,
    IsolatedPostgres,
    assert_no_open_transaction,
    database_settings,
    run_alembic,
    version_rows,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
async def records(database: Database) -> Table:
    # 테스트 전용 테이블은 Alembic이 사용하는 애플리케이션 메타데이터에 포함하지 않는다.
    metadata = MetaData()
    records = Table(
        "session_records",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("content", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    async with database.engine.begin() as connection:
        await connection.execute(CreateTable(records))
    return records


@pytest.mark.asyncio
async def test_alembic_round_trip_and_schema_drift_detection(
    postgres: IsolatedPostgres, database: Database
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == ["0009_context_compaction"]

    await run_alembic(postgres, "upgrade", "0001_database_baseline")
    assert await version_rows(database) == ["0001_database_baseline"]
    await run_alembic(postgres, "upgrade", "head")
    assert await version_rows(database) == heads
    await run_alembic(postgres, "check")

    # 데이터가 없는 독립 DB에서 압축 리비전만 되돌린 뒤 다시 적용한다.
    await run_alembic(postgres, "downgrade", "-1")
    assert await version_rows(database) == ["0008_deferred_charging"]
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        generation_columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns("generation_runs")
        )
    assert "context_compaction_needed" not in {column["name"] for column in generation_columns}
    assert set(tables) == {
        "alembic_version",
        "users",
        "workspaces",
        "workspace_members",
        "conversations",
        "messages",
        "usage_plans",
        "token_budgets",
        "token_reservations",
        "auth_identities",
        "auth_sessions",
        "generation_runs",
        "generation_events",
    }
    await run_alembic(postgres, "upgrade", "head")
    await run_alembic(postgres, "check")
    async with database.engine.connect() as connection:
        assert await connection.run_sync(
            lambda sync: inspect(sync).has_table("conversation_compactions")
        )

    await run_alembic(postgres, "downgrade", "base")
    assert await version_rows(database) == []
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert set(tables) <= {"alembic_version"}

    await run_alembic(postgres, "upgrade", "head")
    assert await version_rows(database) == heads
    await run_alembic(postgres, "check")

    # "check"가 PostgreSQL과 애플리케이션 메타데이터를 실제로 비교하는지 검증한다.
    async with database.engine.begin() as connection:
        await connection.execute(
            sql("CREATE TABLE unexpected_schema_drift (id integer PRIMARY KEY)")
        )
    await run_alembic(postgres, "check", success=False)


@pytest.mark.asyncio
async def test_session_explicit_commit_persists_utf8_and_utc_timestamp(
    database: Database, postgres: IsolatedPostgres, records: Table
) -> None:
    async with database.session() as session:
        assert await session.scalar(sql("SHOW TIME ZONE")) == "UTC"
        await session.execute(records.insert().values(id=1, content="한글 저장 확인"))
        await session.commit()

    async with database.session() as session:
        row = (await session.execute(select(records))).one()
        assert row.content == "한글 저장 확인"
        assert row.created_at.tzinfo is not None
        assert row.created_at.utcoffset() == timedelta(0)

    await assert_no_open_transaction(database, postgres)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["normal", "exception", "cancelled"])
async def test_session_exit_rolls_back_and_returns_connection(
    database: Database, postgres: IsolatedPostgres, records: Table, exit_mode: str
) -> None:
    entered = asyncio.Event()

    async def write_without_commit() -> None:
        async with database.session() as session:
            await session.execute(records.insert().values(id=1, content="must be rolled back"))
            entered.set()
            if exit_mode == "exception":
                raise RuntimeError("request failed before commit")
            if exit_mode == "cancelled":
                await asyncio.Event().wait()

    if exit_mode == "exception":
        with pytest.raises(RuntimeError, match="request failed before commit"):
            await write_without_commit()
    elif exit_mode == "cancelled":
        task = asyncio.create_task(write_without_commit())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    else:
        await write_without_commit()

    await assert_no_open_transaction(database, postgres)
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(records)) == 0


@pytest.mark.asyncio
async def test_database_error_rolls_back_transaction_and_pool_remains_usable(
    database: Database, postgres: IsolatedPostgres, records: Table
) -> None:
    with pytest.raises(IntegrityError):
        async with database.session() as session:
            await session.execute(records.insert().values(id=1, content="first"))
            await session.execute(records.insert().values(id=1, content="duplicate primary key"))

    await assert_no_open_transaction(database, postgres)
    await database.check_connection()
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(records)) == 0


@pytest.mark.asyncio
async def test_request_session_is_released_before_streaming_begins(
    postgres: IsolatedPostgres, records: Table
) -> None:
    settings = database_settings(postgres)
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    stream_connection_counts: list[int] = []

    @app.get("/test/session-stream")
    async def stream_with_session(session: DBSession) -> StreamingResponse:
        await session.execute(records.insert().values(id=1, content="uncommitted request data"))

        async def events() -> AsyncIterator[str]:
            current_database: Database = app.state.database
            stream_connection_counts.append(current_database.engine.pool.checkedout())
            async with current_database.session() as stream_session:
                count = await stream_session.scalar(select(func.count()).select_from(records))
            yield f"data: {count}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/test/session-stream")
        assert response.status_code == 200
        assert response.text == "data: 0\n\n"
        assert stream_connection_counts == [0]
        await assert_no_open_transaction(app.state.database, postgres)


@pytest.mark.asyncio
async def test_readiness_recovers_after_real_database_connection_failure(
    postgres: IsolatedPostgres,
) -> None:
    settings = database_settings(postgres)
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        healthy = await client.get("/health/ready")
        assert healthy.status_code == 200
        assert healthy.json() == {
            "status": "ready",
            "checks": {"database": "ready", "model": "ready"},
        }

        await postgres.set_connections_allowed(False)
        try:
            await postgres.terminate_connections()
            unavailable = await client.get("/health/ready")
            assert unavailable.status_code == 503
            assert unavailable.json() == {
                "status": "not_ready",
                "checks": {"database": "unavailable", "model": "ready"},
            }
            live = await client.get("/health/live")
            assert live.status_code == 200
            assert live.json() == {"status": "ok"}
        finally:
            await postgres.set_connections_allowed(True)

        recovered = await client.get("/health/ready")
        assert recovered.status_code == 200
        assert recovered.json()["checks"]["database"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("exceptional_exit", [False, True])
async def test_lifespan_disposes_real_database_connections(
    postgres: IsolatedPostgres, exceptional_exit: bool
) -> None:
    settings = database_settings(postgres)
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))

    async def use_lifespan() -> None:
        async with app.router.lifespan_context(app):
            await app.state.database.check_connection()
            assert await postgres.active_connection_count() > 0
            if exceptional_exit:
                raise RuntimeError("application shutdown after failure")

    if exceptional_exit:
        with pytest.raises(RuntimeError, match="application shutdown after failure"):
            await use_lifespan()
    else:
        await use_lifespan()

    assert await postgres.active_connection_count() == 0
