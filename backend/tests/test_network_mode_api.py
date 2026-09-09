"""실제 사용자 세션과 PostgreSQL에서 네트워크 설정의 권한·세대·격리를 검증한다."""

import asyncio
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, update

from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app
from backend.app.models.user_preferences import UserPreference
from backend.app.models.users import User
from backend.app.repositories import AccessDenied
from backend.app.services.network_mode import NetworkModeService
from backend.tests.conftest import database_settings
from backend.tests.test_network_mode import CheckingProvider

pytestmark = pytest.mark.postgres
ORIGIN = "http://192.168.1.20:3000"
WRITE_HEADERS = {"Origin": ORIGIN, "X-Project-LLM-Origin": ORIGIN}


@pytest.fixture
async def network_client(schema_database, postgres):
    settings = database_settings(postgres)
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    checking = CheckingProvider()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8000",
            headers=WRITE_HEADERS,
        ) as client,
    ):
        app.state.network_mode = NetworkModeService(app.state.database, settings, checking)
        yield client, app, checking


async def signup(client, suffix="first"):
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": f"network-{suffix}@example.com",
            "display_name": "네트워크 테스트",
            "password": "Network-Test-123!",
        },
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return UUID(response.json()["user"]["id"])


async def test_modes_require_auth_and_mutations_require_csrf_origin_and_strict_json(network_client):
    client, _, checking = network_client
    assert (await client.get("/api/v1/network-mode")).status_code == 401
    await signup(client)
    csrf = client.headers.pop("X-CSRF-Token")
    for method, path, payload in (
        ("PATCH", "/api/v1/network-mode", {"local_only": True}),
        ("POST", "/api/v1/network-mode/check", {}),
    ):
        assert (await client.request(method, path, json=payload)).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    for payload in (
        {"local_only": "true"},
        {"local_only": 1},
        {"local_only": True, "user_id": "x"},
    ):
        assert (await client.patch("/api/v1/network-mode", json=payload)).status_code == 422
    assert (
        await client.patch(
            "/api/v1/network-mode",
            json={"local_only": True},
            headers={"Origin": "https://outside.example"},
        )
    ).status_code == 403
    assert (
        await client.get("/api/v1/network-mode", params={"user_id": "another"})
    ).status_code == 422
    assert checking.checks == 0


async def test_preference_persists_across_services_and_toggle_revision_is_monotonic(network_client):
    client, app, checking = network_client
    user_id = await signup(client)
    default = await client.get("/api/v1/network-mode")
    assert default.status_code == 200
    assert default.headers["cache-control"] == "no-store"
    assert default.json()["local_only"] is False
    assert default.json()["revision"] == 0
    assert default.json()["mode"] == "online"
    assert checking.checks == 1
    for local_only, revision in ((True, 1), (True, 1), (False, 2), (False, 2), (True, 3)):
        result = await client.patch("/api/v1/network-mode", json={"local_only": local_only})
        assert result.status_code == 200, result.text
        assert result.json()["revision"] == revision
        assert result.json()["local_only"] == local_only
    replacement = NetworkModeService(app.state.database, app.state.settings, CheckingProvider())
    assert (await replacement.status(user_id))["revision"] == 3
    assert replacement.provider.checks == 0
    assert not await replacement.is_allowed(user_id, 0)
    async with app.state.database.session() as session:
        saved = await session.get(UserPreference, user_id)
        assert saved.local_only is True
        assert saved.updated_at.tzinfo is not None
    assert app.state.database.engine.pool.checkedout() == 0


async def test_each_account_owns_its_preference_and_local_mode_never_probes(network_client):
    client, app, checking = network_client
    first = await signup(client)
    assert (
        await client.patch("/api/v1/network-mode", json={"local_only": True})
    ).status_code == 200
    assert (await client.post("/api/v1/network-mode/check", json={})).json()["mode"] == "local"
    assert checking.checks == 0
    client.cookies.clear()
    second = await signup(client, "second")
    response = await client.get("/api/v1/network-mode")
    assert response.json()["local_only"] is False
    assert response.json()["revision"] == 0
    assert checking.checks == 1
    async with app.state.database.session() as session:
        assert (await session.get(UserPreference, first)).local_only is True
        assert await session.get(UserPreference, second) is None


async def test_local_toggle_cancels_slow_status_without_holding_database_connection(network_client):
    client, app, checking = network_client
    await signup(client)
    checking.block = True
    pending = asyncio.create_task(client.get("/api/v1/network-mode"))
    await asyncio.wait_for(checking.started.wait(), 1)
    changed = await asyncio.wait_for(
        client.patch("/api/v1/network-mode", json={"local_only": True}), 1
    )
    assert changed.json()["mode"] == "local"
    assert (await asyncio.wait_for(pending, 1)).json()["mode"] == "local"
    assert checking.cancelled.is_set()
    assert app.state.database.engine.pool.checkedout() == 0


async def test_missing_key_and_offline_states_do_not_change_saved_auto_preference(network_client):
    client, app, checking = network_client
    user_id = await signup(client)
    checking.configured = False
    missing_key = await client.get("/api/v1/network-mode")
    assert missing_key.json()["reason"] == "provider_unconfigured"
    assert checking.checks == 0
    checking.configured = True
    checking.available = False
    disconnected = await client.post("/api/v1/network-mode/check", json={})
    assert disconnected.json()["reason"] == "offline"
    assert disconnected.json()["local_only"] is False
    checking.available = True
    connected = await client.post("/api/v1/network-mode/check", json={})
    assert connected.json()["mode"] == "online"
    assert connected.json()["revision"] == 0
    async with app.state.database.session() as session:
        assert await session.get(UserPreference, user_id) is None


async def test_disabled_user_cannot_change_settings_or_reuse_search_permission(network_client):
    client, app, checking = network_client
    user_id = await signup(client)
    async with app.state.database.session() as session:
        await session.execute(update(User).where(User.id == user_id).values(status="disabled"))
        await session.commit()
    assert (await client.get("/api/v1/network-mode")).status_code == 401
    assert not await app.state.network_mode.is_allowed(user_id, 0)
    with pytest.raises(AccessDenied):
        await app.state.network_mode.set_local_only(user_id, True)
    async with app.state.database.session() as session:
        assert list((await session.scalars(select(UserPreference))).all()) == []
    assert checking.checks == 0
