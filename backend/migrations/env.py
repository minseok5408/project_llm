"""명시적 연결 또는 환경변수로 지정한 소유자 URL을 사용하는 비동기 마이그레이션."""

import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.config import Settings
from backend.app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # SQL 생성에는 인증정보나 실행 중인 PostgreSQL 인스턴스가 필요하지 않다.
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    settings = Settings()
    url = settings.migration_database_url or settings.database_url
    if url is None:
        raise CommandError("Set MIGRATION_DATABASE_URL or DATABASE_URL to run migrations")
    # URL을 직접 전달해 ConfigParser가 인코딩된 비밀번호를 보간하지 않게 한다.
    engine = create_async_engine(
        url.get_secret_value(),
        poolclass=pool.NullPool,
        echo=False,
        hide_parameters=True,
        connect_args={
            "timeout": settings.database_connect_timeout_seconds,
            "server_settings": {"timezone": "UTC"},
        },
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        asyncio.run(run_async_migrations())
    else:
        # 테스트와 도구가 AsyncConnection.run_sync()로 트랜잭션을 공유할 수 있게 한다.
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
