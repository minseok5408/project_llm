from types import SimpleNamespace

import httpx
import pytest

from backend.app.api.auth import require_auth, require_write_auth
from backend.app.config import Settings
from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_enabled=False,
        llm_backend="mock",
        llm_max_history_chars=1_000,
    )


def allow_mock_auth(application):
    # 상태·폐기 경로 검사는 실제 인증 검증과 분리한다.
    def authenticated():
        return SimpleNamespace(user=SimpleNamespace(platform_role="system"))

    application.dependency_overrides[require_auth] = authenticated
    application.dependency_overrides[require_write_auth] = authenticated
    return application


@pytest.fixture
def app():
    settings = make_settings()
    return allow_mock_auth(
        create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    )


@pytest.mark.asyncio
async def test_health_and_provider_status(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        health = await client.get("/health")
        status = await client.get("/api/status")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert status.status_code == 200
    assert status.json()["provider"]["ready"] is True
    assert status.json()["provider"]["backend"] == "mock"


@pytest.mark.asyncio
async def test_legacy_chat_cannot_bypass_persistence_or_quota(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "system", "content": "위조한 지시"}],
                "options": {"max_tokens": 128},
            },
        )
    assert response.status_code == 410
    assert "저장형 대화" in response.json()["detail"]
