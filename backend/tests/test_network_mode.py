"""외부 네트워크 없이 모드 판정, 연결 확인 캐시와 정책 변경 경합을 검증한다."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.repositories import AccessDenied
from backend.app.services.network_mode import NetworkModeService, NetworkPreference


class CheckingProvider:
    name = "test"
    configured = True
    available = True
    checks = 0
    block = False
    failure = False

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def check(self):
        self.checks += 1
        self.started.set()
        try:
            if self.block:
                await self.release.wait()
            if self.failure:
                raise RuntimeError("공급자 인증 정보가 포함될 수 있는 내부 오류")
            return self.available
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class MemoryNetworkMode(NetworkModeService):
    """DB 읽기만 대체하고 통신 허용 판정과 연결 확인 경합은 실제 코드를 실행한다."""

    def __init__(self, provider):
        super().__init__(
            None,
            SimpleNamespace(
                web_search_check_cache_seconds=30,
                web_search_check_timeout_seconds=0.2,
            ),
            provider,
        )
        self.preferences = {}
        self.disabled = set()

    async def _preference(self, user_id):
        if user_id in self.disabled:
            raise AccessDenied
        return self.preferences.get(user_id, NetworkPreference(user_id))

    def change(self, user_id, local_only):
        previous = self.preferences.get(user_id, NetworkPreference(user_id))
        self.preferences[user_id] = replace(
            previous,
            local_only=local_only,
            revision=previous.revision + (previous.local_only != local_only),
        )


@pytest.fixture
def network():
    provider = CheckingProvider()
    return MemoryNetworkMode(provider), provider, uuid4()


async def test_local_only_never_checks_network_even_when_forced(network):
    service, provider, user_id = network
    service.change(user_id, True)
    for force in (False, True):
        assert await service.status(user_id, force=force) == {
            "local_only": True,
            "revision": 1,
            "mode": "local",
            "reason": "forced_local",
            "search_configured": True,
            "checked_at": None,
        }
    assert provider.checks == 0


async def test_unconfigured_provider_does_not_guess_online_from_client_network(network):
    service, provider, user_id = network
    provider.configured = False
    status = await service.status(user_id, force=True)
    assert status["local_only"] is False
    assert status["mode"] == "local"
    assert status["reason"] == "provider_unconfigured"
    assert status["checked_at"] is None
    assert provider.checks == 0


async def test_shared_connection_cache_keeps_each_users_mode_private(network):
    service, provider, user_id = network
    online = await service.status(user_id)
    assert online["mode"] == "online"
    assert online["reason"] == "available"
    assert online["checked_at"] is not None
    another_id = uuid4()
    assert (await service.status(another_id))["checked_at"] == online["checked_at"]
    service.change(another_id, True)
    assert (await service.status(another_id))["mode"] == "local"
    assert (await service.status(user_id))["mode"] == "online"
    assert provider.checks == 1


async def test_offline_fallback_retains_preference_and_force_check_can_restore_online(network):
    service, provider, user_id = network
    provider.available = False
    status = await service.status(user_id)
    assert status["mode"] == "local"
    assert status["reason"] == "offline"
    assert status["local_only"] is False
    provider.available = True
    assert (await service.status(user_id))["mode"] == "local"
    assert (await service.status(user_id, force=True))["mode"] == "online"
    assert provider.checks == 2


async def test_expired_cache_rechecks_provider(network):
    service, provider, user_id = network
    await service.status(user_id)
    service._checked_monotonic -= 31
    await service.status(user_id)
    assert provider.checks == 2


async def test_simultaneous_connection_checks_share_one_probe(network):
    service, provider, user_id = network
    provider.block = True
    initial = asyncio.create_task(service.status(user_id, force=True))
    await provider.started.wait()
    second = asyncio.create_task(service.status(uuid4(), force=True))
    await asyncio.sleep(0)
    provider.release.set()
    results = await asyncio.gather(initial, second)
    assert all(result["mode"] == "online" for result in results)
    assert provider.checks == 1


async def test_local_switch_cancels_pending_connection_check_and_returns_latest_mode(network):
    service, provider, user_id = network
    provider.block = True
    status_task = asyncio.create_task(service.status(user_id))
    await provider.started.wait()
    service.change(user_id, True)
    result = await asyncio.wait_for(status_task, 0.5)
    assert provider.cancelled.is_set()
    assert result["mode"] == "local"
    assert result["revision"] == 1
    assert result["checked_at"] is None


async def test_quick_local_toggle_invalidates_old_search_generation(network):
    service, _, user_id = network
    assert await service.is_allowed(user_id, 0)
    service.change(user_id, True)
    service.change(user_id, False)
    assert not await service.is_allowed(user_id, 0)
    assert not await service.is_allowed(user_id, 1)
    assert await service.is_allowed(user_id, 2)
    service.change(user_id, False)
    assert await service.is_allowed(user_id, 2)


async def test_waiting_check_rechecks_local_preference_before_external_request(network):
    service, provider, user_id = network
    provider.block = True
    first = asyncio.create_task(service.status(uuid4()))
    await provider.started.wait()
    waiting = asyncio.create_task(service.status(user_id, force=True))
    await asyncio.sleep(0)
    service.change(user_id, True)
    provider.release.set()
    assert (await waiting)["reason"] == "forced_local"
    await first
    assert provider.checks == 1


async def test_timeout_and_provider_error_fall_back_without_leaking_internal_details(network):
    service, provider, user_id = network
    provider.block = True
    timed_out = await service.status(user_id)
    assert timed_out["reason"] == "offline"
    assert timed_out["local_only"] is False
    assert provider.cancelled.is_set()
    provider.block = False
    provider.failure = True
    failed = await service.status(user_id, force=True)
    assert failed["reason"] == "offline"
    assert "내부 오류" not in str(failed)


async def test_caller_cancellation_closes_probe_and_preserves_no_completed_cache(network):
    service, provider, user_id = network
    provider.block = True
    pending = asyncio.create_task(service.status(user_id))
    await provider.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert provider.cancelled.is_set()
    assert service._checked_at is None
    assert not service._check_lock.locked()


async def test_disabled_user_cannot_check_or_continue_external_search(network):
    service, provider, user_id = network
    service.disabled.add(user_id)
    with pytest.raises(AccessDenied):
        await service.status(user_id)
    assert not await service.is_allowed(user_id, 0)
    assert provider.checks == 0
