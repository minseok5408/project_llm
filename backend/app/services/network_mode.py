"""사용자 설정과 서버의 검색 연결 상태를 분리해 외부 통신을 제한한다."""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models.user_preferences import UserPreference
from backend.app.models.users import User
from backend.app.repositories import AccessDenied


class ConnectivityProvider(Protocol):
    configured: bool
    name: str

    async def check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class NetworkPreference:
    user_id: UUID
    local_only: bool = False
    revision: int = 0


class NetworkModeService:
    """접속 장애는 사용자 설정을 바꾸지 않으며 캐시는 공급자 상태에만 적용한다."""

    def __init__(
        self, database: Database, settings: Settings, provider: ConnectivityProvider
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider = provider
        self._check_lock = asyncio.Lock()
        self._checked_monotonic: float | None = None
        self._checked_at: datetime | None = None
        self._available = False

    async def preference(self, session: AsyncSession, user_id: UUID) -> NetworkPreference:
        user_status = await session.scalar(select(User.status).where(User.id == user_id))
        if user_status != "active":
            raise AccessDenied
        row = await session.get(UserPreference, user_id)
        return NetworkPreference(
            user_id=user_id,
            local_only=row.local_only if row is not None else False,
            revision=row.revision if row is not None else 0,
        )

    async def _preference(self, user_id: UUID) -> NetworkPreference:
        async with self.database.session() as session:
            return await self.preference(session, user_id)

    async def is_allowed(self, user_id: UUID, revision: int) -> bool:
        try:
            current = await self._preference(user_id)
        except AccessDenied:
            return False
        return not current.local_only and current.revision == revision

    async def set_local_only(self, user_id: UUID, local_only: bool) -> dict:
        async with self.database.session() as session:
            # 생성 승인과 같은 사용자 행을 먼저 잠가 동시 설정 변경을 직렬화한다.
            user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None or user.status != "active":
                raise AccessDenied
            row = await session.get(UserPreference, user_id)
            if row is None:
                row = UserPreference(user_id=user_id, local_only=False, revision=0)
                session.add(row)
            if row.local_only != local_only:
                row.local_only = local_only
                row.revision += 1
            await session.commit()
        return await self.status(user_id)

    def _payload(self, preference: NetworkPreference) -> dict:
        configured = self.provider.configured
        if preference.local_only:
            reason, mode = "forced_local", "local"
        elif not configured:
            reason, mode = "provider_unconfigured", "local"
        elif self._available:
            reason, mode = "available", "online"
        else:
            reason, mode = "offline", "local"
        return {
            "local_only": preference.local_only,
            "revision": preference.revision,
            "mode": mode,
            "reason": reason,
            "search_configured": configured,
            "checked_at": (
                self._checked_at.isoformat()
                if configured and not preference.local_only and self._checked_at is not None
                else None
            ),
        }

    async def status(self, user_id: UUID, force: bool = False) -> dict:
        preference = await self._preference(user_id)
        if not preference.local_only and self.provider.configured:
            await self._check(user_id, preference.revision, force=force)
            # 연결 확인 중 다른 탭에서 로컬 모드를 켜도 오래된 온라인 상태를 반환하지 않는다.
            preference = await self._preference(user_id)
        return self._payload(preference)

    async def _watch_preference(self, user_id: UUID, revision: int) -> None:
        while True:
            await asyncio.sleep(0.05)
            if not await self.is_allowed(user_id, revision):
                return

    async def _bounded_check(self) -> bool:
        try:
            async with asyncio.timeout(self.settings.web_search_check_timeout_seconds):
                return bool(await self.provider.check())
        except Exception:
            # 검색 공급자의 주소·응답·인증 정보가 사용자 오류나 로그로 유출되지 않게 한다.
            return False

    async def _check(self, user_id: UUID, revision: int, *, force: bool) -> None:
        requested_at = time.monotonic()
        async with self._check_lock:
            if not await self.is_allowed(user_id, revision):
                return
            if self._checked_monotonic is not None:
                age = time.monotonic() - self._checked_monotonic
                # 동시에 들어온 강제 확인도 대기 중 이미 완료된 한 번의 결과를 공유한다.
                already_refreshed = self._checked_monotonic >= requested_at
                if already_refreshed or (
                    not force and age < self.settings.web_search_check_cache_seconds
                ):
                    return
            check = asyncio.create_task(self._bounded_check())
            guard = asyncio.create_task(self._watch_preference(user_id, revision))
            try:
                await asyncio.wait({check, guard}, return_when=asyncio.FIRST_COMPLETED)
                if guard.done():
                    # 감시 DB 오류도 외부 통신을 허용하는 근거가 될 수 없다.
                    guard.result()
                    return
                available = check.result()
                if not await self.is_allowed(user_id, revision):
                    return
                self._available = available
                self._checked_at = datetime.now(UTC)
                self._checked_monotonic = time.monotonic()
            finally:
                for task in (check, guard):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(check, guard, return_exceptions=True)
