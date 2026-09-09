"""완료된 중단 신호 뒤 외부 코루틴이 시작하지 않는 경계를 검증한다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.runtime.cancellation import GenerationCancelled
from backend.app.tools.web_search.service import WebSearchService


@pytest.mark.parametrize("cancel_during_permission", [False, True])
async def test_guard_closes_unstarted_operation_when_cancelled(cancel_during_permission):
    started = False
    cancellation = asyncio.create_task(asyncio.sleep(0))

    async def operation():
        nonlocal started
        started = True

    async def permission(*_args):
        await cancellation
        return True

    if not cancel_during_permission:
        await cancellation
    service = WebSearchService(
        SimpleNamespace(
            database=None,
            settings=None,
            network_mode=SimpleNamespace(is_allowed=AsyncMock(side_effect=permission)),
        )
    )
    with pytest.raises(GenerationCancelled):
        await service._guarded(operation(), {"user_id": 1, "network_revision": 0}, cancellation)
    assert not started


async def test_preparation_failure_always_closes_pending_search():
    service = WebSearchService(SimpleNamespace(database=None, settings=None))
    service._prepare = AsyncMock(side_effect=RuntimeError("검증용 실패"))
    service._close_pending = AsyncMock()
    with pytest.raises(RuntimeError):
        await service.prepare({"id": 1}, None)
    service._close_pending.assert_awaited_once_with({"id": 1}, "failed", "preparation_failed")
