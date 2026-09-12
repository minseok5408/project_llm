"""실제 세션·PostgreSQL에서 개인 기억의 소유권과 동시 수정 정책을 검증한다."""

import httpx
import pytest
from sqlalchemy import select

from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app
from backend.app.models import User, UserMemory
from backend.tests.conftest import database_settings, run_alembic, version_rows
from backend.tests.test_network_mode_api import signup

pytestmark = pytest.mark.postgres


@pytest.fixture
async def memory_client(schema_database, postgres):
    settings = database_settings(postgres)
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8000",
            headers={
                "Origin": "http://localhost:3000",
                "X-Project-LLM-Origin": "http://localhost:3000",
            },
        ) as client,
    ):
        yield client, app


async def test_memory_auth_csrf_and_strict_payload(memory_client):
    client, _ = memory_client
    assert (await client.get("/api/v1/memories")).status_code == 401
    await signup(client)
    csrf = client.headers.pop("X-CSRF-Token")
    body = {"revision": 0, "key": "이름", "content": "김민석"}
    assert (await client.post("/api/v1/memories", json=body)).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    for patch in (
        {"user_id": "x"},
        {"revision": True},
        {"content": " "},
        {"key": " "},
        {"content": "가" * 501},
    ):
        assert (await client.post("/api/v1/memories", json={**body, **patch})).status_code == 422
    assert (
        await client.post(
            "/api/v1/memories", json=body, headers={"Origin": "https://outside.example"}
        )
    ).status_code == 403
    assert (await client.get("/api/v1/memories?user_id=other")).status_code == 422
    assert (await client.get("/api/v1/memories")).json()["items"] == []


async def test_memory_crud_persistence_and_system_isolation(memory_client):
    client, app = memory_client
    owner = await signup(client)
    response = await client.post(
        "/api/v1/memories", json={"revision": 0, "key": "직업", "content": "개발자"}
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["items"][0]
    assert response.json()["revision"] == 1
    path = f"/api/v1/memories/{item['id']}"
    first_cookies, first_csrf = dict(client.cookies), client.headers["X-CSRF-Token"]
    client.cookies.clear()
    other = await signup(client, "second")
    async with app.state.database.session() as session:
        (await session.get(User, other)).platform_role = "system"
        await session.commit()
    assert (await client.get("/api/v1/memories")).json()["items"] == []
    assert (
        await client.patch(path, json={"revision": 0, "key": "직업", "content": "침입"})
    ).status_code == 404
    assert (await client.request("DELETE", path, json={"revision": 0})).status_code == 404
    client.cookies.clear()
    client.cookies.update(first_cookies)
    client.headers["X-CSRF-Token"] = first_csrf
    assert (await client.get("/api/v1/memories")).json()["items"] == [item]
    changed = await client.patch(
        path, json={"revision": 1, "key": "직업", "content": "백엔드 개발자"}
    )
    assert changed.json()["revision"] == 2
    assert (
        await client.patch(path, json={"revision": 1, "key": "직업", "content": "옛 값"})
    ).status_code == 409
    unchanged = await client.patch(
        path, json={"revision": 2, "key": "직업", "content": "백엔드 개발자"}
    )
    assert unchanged.json()["revision"] == 2
    deleted = await client.request("DELETE", path, json={"revision": 2})
    assert deleted.json() == {"revision": 3, "limit": 20, "items": []}
    async with app.state.database.session() as session:
        assert await session.scalar(select(UserMemory).where(UserMemory.user_id == owner)) is None
        assert (await session.get(User, owner)).memory_revision == 3


async def test_memory_capacity_duplicates_and_rollback_guard(memory_client, postgres):
    client, _ = memory_client
    await signup(client)
    for index in range(20):
        response = await client.post(
            "/api/v1/memories",
            json={"revision": index, "key": f"선호 {index}", "content": "짧은 답변"},
        )
        assert response.status_code == 201
    for key in ("선호 20", "선호 0"):
        assert (
            await client.post(
                "/api/v1/memories", json={"revision": 20, "key": key, "content": "추가"}
            )
        ).status_code == 409
    await run_alembic(postgres, "downgrade", "0013_generation_steps", success=False)
    assert await version_rows(memory_client[1].state.database) == ["0017_schema_roles"]
    assert len((await client.get("/api/v1/memories")).json()["items"]) == 20


async def test_memory_database_failure_returns_private_unavailable(memory_client, postgres):
    client, _ = memory_client
    await signup(client)
    await postgres.set_connections_allowed(False)
    await postgres.terminate_connections()
    try:
        response = await client.get("/api/v1/memories")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert "password" not in response.text
    finally:
        await postgres.set_connections_allowed(True)
