"""실제 PostgreSQL의 독립된 테스트 데이터베이스와 공통 검증 도구."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text as sql
from sqlalchemy.engine import make_url

from backend.app.config import Settings
from backend.app.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class IsolatedPostgres:
    name: str
    url: str = field(repr=False)
    admin: asyncpg.Connection = field(repr=False)

    async def set_connections_allowed(self, allowed: bool) -> None:
        # name은 사용자 입력이 아니라 아래의 고정 접두사와 uuid.hex로 생성한다.
        value = "true" if allowed else "false"
        await self.admin.execute(f'ALTER DATABASE "{self.name}" ALLOW_CONNECTIONS {value}')

    async def terminate_connections(self) -> None:
        await self.admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            self.name,
        )

    async def active_connection_count(self) -> int:
        return await self.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = $1", self.name
        )


@pytest.fixture
async def postgres() -> AsyncIterator[IsolatedPostgres]:
    configured_url = os.environ.get("TEST_DATABASE_URL")
    if not configured_url:
        pytest.skip(
            "Real PostgreSQL required: run python scripts/test_db.py or set TEST_DATABASE_URL"
        )

    admin_url = make_url(configured_url)
    if admin_url.drivername not in {"postgresql", "postgresql+asyncpg"}:
        pytest.fail("TEST_DATABASE_URL must use postgresql or postgresql+asyncpg")
    if not admin_url.database or not (
        admin_url.database == "qwen_test" or admin_url.database.startswith("qwen_test_")
    ):
        pytest.fail("TEST_DATABASE_URL must target a dedicated qwen_test or qwen_test_* database")

    # 설정된 데이터베이스에 연결할 수 없으면 통합 테스트를 건너뛰지 않고 실패 처리한다.
    admin = await asyncpg.connect(
        admin_url.set(drivername="postgresql").render_as_string(hide_password=False), timeout=5
    )
    database_name = f"qwen_test_{uuid4().hex}"
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        created = True
        yield IsolatedPostgres(
            name=database_name,
            url=admin_url.set(
                drivername="postgresql+asyncpg", database=database_name
            ).render_as_string(hide_password=False),
            admin=admin,
        )
    finally:
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{database_name}" WITH (FORCE)')
        finally:
            await admin.close()


def database_settings(postgres: IsolatedPostgres) -> Settings:
    return Settings(
        _env_file=None,
        llm_backend="mock",
        database_enabled=True,
        generation_worker_enabled=False,
        database_url=postgres.url,
        database_pool_size=1,
        database_max_overflow=0,
        database_pool_timeout_seconds=1,
        database_connect_timeout_seconds=1,
        database_health_timeout_seconds=1,
    )


@pytest.fixture
async def database(postgres: IsolatedPostgres) -> AsyncIterator[Database]:
    database = Database(database_settings(postgres))
    try:
        yield database
    finally:
        await database.dispose()


async def run_alembic(postgres: IsolatedPostgres, *arguments: str, success: bool = True) -> None:
    environment = os.environ.copy()
    environment.update(
        DATABASE_ENABLED="true",
        DATABASE_URL=postgres.url,
        MIGRATION_DATABASE_URL=postgres.url,
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "-c", str(PROJECT_ROOT / "alembic.ini"), *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stdout + result.stderr


async def version_rows(database: Database) -> list[str]:
    async with database.session() as session:
        return list((await session.scalars(sql("SELECT version_num FROM alembic_version"))).all())


async def assert_no_open_transaction(database: Database, postgres: IsolatedPostgres) -> None:
    assert database.engine.pool.checkedout() == 0
    assert (
        await postgres.admin.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = $1 AND state LIKE 'idle in transaction%'",
            postgres.name,
        )
        == 0
    )


@pytest.fixture
async def schema_database(postgres: IsolatedPostgres, database: Database) -> Database:
    """실제 마이그레이션을 적용한 데이터베이스를 제공한다."""
    await run_alembic(postgres, "upgrade", "head")
    return database
