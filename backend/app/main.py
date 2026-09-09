from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.auth import (
    AuthNoStoreMiddleware,
    AuthRateLimiter,
    CurrentAuth,
    WriteAuth,
)
from backend.app.api.auth import router as auth_router
from backend.app.api.conversations import router as conversations_router
from backend.app.api.generations import router as generations_router
from backend.app.api.health import router as health_router
from backend.app.api.usage import router as usage_router
from backend.app.config import Settings, get_settings
from backend.app.db import Database
from backend.app.providers import ChatProvider, build_provider
from backend.app.schemas import StatusResponse
from backend.app.services.generations import GenerationService, GenerationWorker


def create_app(
    *,
    settings: Settings | None = None,
    provider: ChatProvider | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_provider = provider or build_provider(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = app_settings
        application.state.provider = app_provider
        application.state.auth_rate_limiter = AuthRateLimiter()
        application.state.database = (
            Database(app_settings) if app_settings.database_enabled else None
        )
        application.state.generations = None
        worker = None
        if application.state.database is not None:
            application.state.generations = GenerationService(
                application.state.database, app_provider, app_settings
            )
            if app_settings.generation_worker_enabled:
                worker = GenerationWorker(application.state.generations)
                worker.start()
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            if application.state.database is not None:
                await application.state.database.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.add_middleware(AuthNoStoreMiddleware)

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(conversations_router)
    application.include_router(generations_router)
    application.include_router(usage_router)

    @application.get("/api/status", response_model=StatusResponse)
    async def status(request: Request, auth: CurrentAuth) -> StatusResponse:
        current_provider: ChatProvider = request.app.state.provider
        provider_status = await current_provider.status()
        return StatusResponse(provider=provider_status)

    @application.post("/api/chat")
    async def legacy_chat(auth: WriteAuth) -> None:
        raise HTTPException(status_code=410, detail="저장형 대화 API를 사용하세요.")

    return application


app = create_app()
