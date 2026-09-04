import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app.config import Settings, get_settings
from backend.app.providers import ChatProvider, ProviderUnavailable, build_provider
from backend.app.schemas import ChatRequest, StatusResponse

logger = logging.getLogger(__name__)


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def create_app(
    *,
    settings: Settings | None = None,
    provider: ChatProvider | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_provider = provider or build_provider(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.provider = app_provider
        application.state.generation_gate = asyncio.Semaphore(
            app_settings.llm_max_concurrent_generations
        )
        yield

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

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/status", response_model=StatusResponse)
    async def status(request: Request) -> StatusResponse:
        current_provider: ChatProvider = request.app.state.provider
        provider_status = await current_provider.status()
        return StatusResponse(provider=provider_status)

    @application.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        history_size = sum(len(message.content) for message in payload.messages)
        if history_size > app_settings.llm_max_history_chars:
            raise HTTPException(
                status_code=413,
                detail="대화가 너무 깁니다. 새 대화를 시작하거나 이전 내용을 줄여주세요.",
            )

        request_id = str(uuid4())

        async def event_stream() -> AsyncIterator[str]:
            current_provider: ChatProvider = request.app.state.provider
            gate: asyncio.Semaphore = request.app.state.generation_gate
            yield encode_sse(
                "meta",
                {"request_id": request_id, "model": app_settings.llm_model_id},
            )

            try:
                async with gate:
                    async for delta in current_provider.stream(payload.messages, payload.options):
                        if await request.is_disconnected():
                            return
                        yield encode_sse("delta", {"request_id": request_id, "text": delta.text})

                yield encode_sse("done", {"request_id": request_id})
            except asyncio.CancelledError:
                raise
            except ProviderUnavailable as error:
                yield encode_sse("error", {"request_id": request_id, "message": str(error)})
            except Exception:
                logger.exception("Unexpected generation failure", extra={"request_id": request_id})
                yield encode_sse(
                    "error",
                    {
                        "request_id": request_id,
                        "message": "응답 생성 중 오류가 발생했습니다.",
                    },
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    return application


app = create_app()
