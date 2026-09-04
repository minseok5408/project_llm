from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.providers import MockProvider, ProviderDelta, ProviderUnavailable
from backend.app.schemas import ChatMessage, GenerationOptions, ProviderStatus


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_backend="mock",
        llm_max_history_chars=1_000,
    )


@pytest.fixture
def app():
    settings = make_settings()
    return create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))


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
async def test_chat_streams_utf8_sse_and_finishes_once(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "한글 연결 테스트"}],
                "options": {"thinking": False, "max_tokens": 128},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: meta" in response.text
    assert "event: delta" in response.text
    assert "한글" in response.text
    assert response.text.count("event: done") == 1
    assert "event: error" not in response.text


@pytest.mark.asyncio
async def test_blank_message_is_rejected(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "   "}]},
        )

    assert response.status_code == 422


class FailingProvider:
    async def status(self) -> ProviderStatus:
        return ProviderStatus(backend="test", ready=False, model="test", detail="offline")

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> AsyncIterator[ProviderDelta]:
        if False:
            yield ProviderDelta(text="")
        raise ProviderUnavailable("테스트용 안전한 오류")


@pytest.mark.asyncio
async def test_provider_failure_becomes_safe_sse_error() -> None:
    settings = make_settings()
    app = create_app(settings=settings, provider=FailingProvider())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "실패 테스트"}]},
        )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "테스트용 안전한 오류" in response.text
    assert "event: done" not in response.text
