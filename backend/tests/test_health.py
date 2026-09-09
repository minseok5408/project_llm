import asyncio
import json
import logging

import httpx
import pytest

from backend.app.config import Settings
from backend.app.db import DBSession
from backend.app.main import create_app
from backend.app.providers import MockProvider
from backend.app.schemas import ProviderStatus


def mock_settings(**overrides) -> Settings:
    return Settings(_env_file=None, llm_backend="mock", **overrides)


async def test_disabled_database_needs_no_connection() -> None:
    app = create_app(settings=mock_settings(database_enabled=False))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        assert app.state.database is None
        for path in ("/health", "/health/live"):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "disabled", "model": "ready"},
    }
    assert response.headers["cache-control"] == "no-store"


class UnavailableProvider(MockProvider):
    async def status(self) -> ProviderStatus:
        return ProviderStatus(backend="mock", ready=False, model="test", detail="private detail")


class BrokenProvider(MockProvider):
    async def status(self) -> ProviderStatus:
        raise RuntimeError("postgresql://private-user:private-password@private-host/db")


@pytest.mark.parametrize("provider_class", [UnavailableProvider, BrokenProvider])
async def test_unavailable_model_changes_only_readiness(provider_class, caplog) -> None:
    settings = mock_settings(database_enabled=False)
    app = create_app(settings=settings, provider=provider_class(settings))
    with caplog.at_level(logging.INFO, logger="backend.app.api.health"):
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
        ):
            response = await client.get("/health/ready")
            assert (await client.get("/health/live")).status_code == 200

    assert response.status_code == 503
    assert response.json()["checks"]["model"] == "unavailable"
    assert "private" not in response.text + caplog.text
    record = next(record for record in caplog.records if record.name == "backend.app.api.health")
    metric = json.loads(record.message)
    assert metric["event"] == "readiness_check"
    assert metric["status_code"] == 503
    assert metric["duration_ms"] >= 0


@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_database_probe_failure_is_bounded_and_redacted(monkeypatch, caplog, failure) -> None:
    class ProbeDatabase:
        disposed = False
        checks = 0

        def __init__(self, settings):
            pass

        async def check_connection(self):
            self.checks += 1
            if failure == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("postgresql://private-user:private-password@private-host/db")

        async def dispose(self):
            self.disposed = True

    monkeypatch.setattr("backend.app.main.Database", ProbeDatabase)
    settings = mock_settings(
        database_enabled=True,
        database_url="postgresql+asyncpg://test:private-password@127.0.0.1/test",
        database_health_timeout_seconds=0.1,
    )
    app = create_app(settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        database = app.state.database
        assert database.checks == 0
        assert (await client.get("/health/live")).status_code == 200
        assert database.checks == 0
        async with asyncio.timeout(1):
            response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "checks": {"database": "unavailable", "model": "ready"},
        }
        assert "private" not in response.text + caplog.text
    assert database.disposed


async def test_disabled_database_dependency_returns_safe_503() -> None:
    app = create_app(settings=mock_settings(database_enabled=False))

    @app.get("/test/db")
    async def requires_database(session: DBSession):
        raise AssertionError("The handler must not run without a database")

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client,
    ):
        response = await client.get("/test/db")
    assert response.status_code == 503
    assert "DATABASE_URL" not in response.text
