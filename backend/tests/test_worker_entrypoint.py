"""네트워크 서버 없이 독립 실행자의 수명과 API 기본 분리를 확인한다."""

import asyncio

import pytest

from backend.app import worker as entrypoint
from backend.app.config import Settings
from backend.app.main import create_app


def settings(**overrides):
    return Settings(
        _env_file=None,
        llm_backend="mock",
        database_url="postgresql+asyncpg://unused:unused@127.0.0.1/test",
        **overrides,
    )


async def test_api_lifespan_does_not_start_or_stop_a_worker_by_default(monkeypatch):
    class ForbiddenWorker:
        def __init__(self, service):
            raise AssertionError("독립 실행자가 기본값이면 API가 실행자를 만들면 안 됩니다.")

    monkeypatch.setattr("backend.app.main.GenerationWorker", ForbiddenWorker)
    config = settings(database_enabled=True)
    assert config.generation_worker_enabled is False
    for _ in range(2):
        app = create_app(settings=config)
        async with app.router.lifespan_context(app):
            assert app.state.generations is not None


async def test_worker_entrypoint_cleans_up_in_order_on_shutdown(monkeypatch):
    calls = []
    stop = asyncio.Event()

    class FakeDatabase:
        def __init__(self, config):
            calls.append("database")

        async def dispose(self):
            calls.append("dispose")

    class FakeWorker:
        def __init__(self, service):
            pass

        def start(self):
            calls.append("start")
            self.task = asyncio.create_task(asyncio.Event().wait())
            stop.set()

        async def stop(self):
            calls.append("settle_and_unlock")
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    monkeypatch.setattr(entrypoint, "Database", FakeDatabase)
    monkeypatch.setattr(entrypoint, "GenerationWorker", FakeWorker)
    await entrypoint.run_worker(settings(database_enabled=True), stop_event=stop)
    assert calls == ["database", "start", "settle_and_unlock", "dispose"]


async def test_worker_requires_enabled_database_before_opening_resources(monkeypatch):
    def forbidden_database(config):
        raise AssertionError("비활성 DB를 생성하지 않아야 합니다.")

    monkeypatch.setattr(entrypoint, "Database", forbidden_database)
    with pytest.raises(ValueError, match="DATABASE_ENABLED"):
        await entrypoint.run_worker(settings(database_enabled=False), stop_event=asyncio.Event())


def test_worker_entrypoint_redacts_startup_errors(monkeypatch, caplog):
    def invalid_settings():
        raise RuntimeError("postgresql://secret-user:secret-password@secret-host/database")

    monkeypatch.setattr(entrypoint, "get_settings", invalid_settings)
    assert entrypoint.main() == 1
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text
