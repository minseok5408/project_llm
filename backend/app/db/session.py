import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.config import Settings


class Database:
    """애플리케이션 수명주기마다 크기가 제한된 비동기 연결 풀 하나를 관리한다."""

    def __init__(self, settings: Settings) -> None:
        if settings.database_url is None:
            raise ValueError("DATABASE_URL is required to initialize the database")
        self._health_timeout_seconds = settings.database_health_timeout_seconds
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_pre_ping=True,
            echo=False,
            hide_parameters=True,
            connect_args={
                "timeout": settings.database_connect_timeout_seconds,
                "server_settings": {"timezone": "UTC"},
            },
        )
        self._session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
            # 연결을 풀에 반환한 의존성이 SSE 반복자 안에서 세션을 다시 열지 못하게 한다.
            close_resets_only=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """독립된 세션을 제공하며, 짧은 트랜잭션의 커밋은 호출자가 명시적으로 수행한다."""
        async with self._session_factory() as session:
            try:
                yield session
            finally:
                # 정상 반환, 예외, 취소 모두 커밋하지 않은 작업을 폐기한다.
                # 롤백 중 예외가 발생해도 세션 컨텍스트 관리자가 세션을 닫는다.
                if session.in_transaction():
                    await session.rollback()

    async def check_connection(self) -> None:
        """준비 상태 확인에서 풀의 연결 획득부터 접속, 쿼리 실행까지 시간 제한을 적용한다."""
        async with (
            asyncio.timeout(self._health_timeout_seconds),
            self.engine.connect() as connection,
        ):
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Database is unavailable")
    async with database.session() as session:
        yield session


# 함수 범위를 사용해 응답 스트리밍을 시작하기 전에 세션을 반환한다(FastAPI >= 0.121).
# 백그라운드 작업과 이후 스트리밍 처리는 별도의 짧은 세션을 얻어야 한다.
DBSession = Annotated[AsyncSession, Depends(get_session, scope="function")]
