"""새 사용자 메시지만 받아 생성 작업을 만들고 저장된 이벤트를 다시 재생한다."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app.api.auth import CurrentAuth, WriteAuth, require_json
from backend.app.api.conversations import (
    EmptyPayload,
    data_session,
    parse_id,
    private_json,
    read_json_payload,
    read_query,
)
from backend.app.context.status import context_status
from backend.app.llm.protocol import ProviderUnavailable
from backend.app.models import GenerationEvent, GenerationRun, TokenReservation, WebSearchRun
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, RepositoryUnavailable
from backend.app.schemas import GenerationOptions
from backend.app.services.auth import AuthService, InvalidSession
from backend.app.services.generations import GenerationService
from backend.app.services.generations.admission import QueueFull
from backend.app.services.generations.events import TERMINAL_STATUSES, accessible_run, run_payload
from backend.app.services.token_quota import QuotaExceeded
from backend.app.tools.questions import AnswerText
from backend.app.tools.web_search.service import search_payload

router = APIRouter(prefix="/api/v1", tags=["generations"])


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    content: str = Field(min_length=1, max_length=100_000)
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    network_mode: Literal["auto", "local"] | None = None
    web_search: Literal["auto", "on", "off"] = "auto"


class EventsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    after: int | None = Field(default=None, ge=0, le=2**63 - 1)


class RegeneratePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    network_mode: Literal["auto", "local"] | None = None
    web_search: Literal["auto", "on", "off"] = "auto"


class QuestionResponsePayload(RegeneratePayload):
    answers: list[AnswerText] = Field(min_length=1, max_length=3)


def idempotency_key(request: Request) -> UUID:
    keys = request.headers.getlist("idempotency-key")
    try:
        if len(keys) != 1:
            raise ValueError
        return UUID(keys[0])
    except ValueError:
        raise HTTPException(
            status_code=422, detail="UUID 형식의 Idempotency-Key가 필요합니다."
        ) from None


def service(request: Request) -> GenerationService:
    result = getattr(request.app.state, "generations", None)
    if result is None:
        raise HTTPException(status_code=503, detail="대화 저장소를 사용할 수 없습니다.")
    return result


@asynccontextmanager
async def generation_errors():
    try:
        yield
    except AccessDenied:
        raise HTTPException(status_code=404, detail="대상을 찾을 수 없습니다.") from None
    except QuotaExceeded:
        raise HTTPException(
            status_code=402,
            detail=("질문과 답변에 사용할 토큰이 부족합니다. 토큰 갱신 후 다시 시도해 주세요."),
        ) from None
    except QueueFull as error:
        raise HTTPException(
            status_code=429, detail=str(error), headers={"Retry-After": "2"}
        ) from None
    except InvalidInput as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except Conflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                "같은 요청이 이미 처리 중이거나 대화가 변경되었습니다. 새로 불러온 뒤 확인하세요."
            ),
        ) from None
    except ProviderUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except (RepositoryUnavailable, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="대화 저장소를 사용할 수 없습니다.") from None


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def send_message(conversation_id: str, request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    require_json(request)
    key = idempotency_key(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 500_000:
            raise HTTPException(status_code=413, detail="메시지가 너무 큽니다.")
    try:
        payload = MessagePayload.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="메시지와 생성 옵션의 형식이 올바르지 않습니다."
        ) from None
    async with generation_errors():
        result = await service(request).submit(
            auth.user.id,
            parse_id(conversation_id),
            content=payload.content,
            options=payload.options,
            idempotency_key=key,
            network_mode=payload.network_mode,
            web_search=payload.web_search,
        )
    return private_json(result, status_code=202)


@router.post("/generations/{generation_id}/regenerate", status_code=202)
async def regenerate_answer(generation_id: str, request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    key = idempotency_key(request)
    payload = await read_json_payload(request, RegeneratePayload)
    async with generation_errors():
        result = await service(request).regenerate(
            auth.user.id,
            parse_id(generation_id),
            options=payload.options,
            idempotency_key=key,
            network_mode=payload.network_mode,
            web_search=payload.web_search,
        )
    return private_json(result, status_code=202)


@router.post("/generations/{generation_id}/respond", status_code=202)
async def respond_to_questions(
    generation_id: str, request: Request, auth: WriteAuth
) -> JSONResponse:
    read_query(request, EmptyPayload)
    key = idempotency_key(request)
    payload = await read_json_payload(request, QuestionResponsePayload, max_bytes=40000)
    async with generation_errors():
        result = await service(request).respond(
            auth.user.id,
            parse_id(generation_id),
            answers=payload.answers,
            options=payload.options,
            idempotency_key=key,
            network_mode=payload.network_mode,
            web_search=payload.web_search,
        )
    return private_json(result, status_code=202)


@router.get("/generations/active")
async def active_generations(request: Request, auth: CurrentAuth) -> JSONResponse:
    """목록 페이지나 현재 작업 공간에 관계없이 본인의 진행 작업을 복원한다."""
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        runs = (
            await session.scalars(
                select(GenerationRun).where(
                    GenerationRun.user_id == auth.user.id,
                    GenerationRun.status.in_(("queued", "running")),
                )
            )
        ).all()
        items = []
        for run in runs:
            try:
                await accessible_run(session, auth.user.id, run.id)
            except AccessDenied:
                continue
            items.append(run_payload(run))
        return private_json({"items": items})


@router.get("/generations/{generation_id}")
async def get_generation(generation_id: str, request: Request, auth: CurrentAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        run = await accessible_run(session, auth.user.id, parse_id(generation_id))
        reservation = await session.get(TokenReservation, run.reservation_id)
        search = await session.scalar(
            select(WebSearchRun).where(WebSearchRun.generation_id == run.id)
        )
        return private_json(
            {
                **run_payload(run),
                "input_tokens": reservation.input_tokens,
                "output_tokens": reservation.output_tokens,
                "usage_basis": reservation.usage_basis,
                "search": search_payload(search),
                "context": await context_status(session, run),
            }
        )


@router.post("/generations/{generation_id}/cancel")
async def cancel_generation(generation_id: str, request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    await read_json_payload(request, EmptyPayload)
    async with generation_errors():
        result = await service(request).cancel(auth.user.id, parse_id(generation_id))
    return private_json(result)


@router.get("/generations/{generation_id}/events")
async def generation_events(
    generation_id: str, request: Request, auth: CurrentAuth
) -> StreamingResponse:
    query = read_query(request, EventsQuery)
    run_id = parse_id(generation_id)
    try:
        headers = request.headers.getlist("last-event-id")
        if len(headers) > 1:
            raise ValueError
        after = query.after if query.after is not None else int(headers[0]) if headers else 0
        if not 0 <= after <= 2**63 - 1:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="이벤트 커서가 올바르지 않습니다.") from None
    async with data_session(request) as session:
        run = await accessible_run(session, auth.user.id, run_id)
        if after > run.last_event_sequence:
            raise HTTPException(
                status_code=422, detail="이벤트 커서가 현재 저장된 범위를 초과했습니다."
            )
    database = service(request).database

    async def stream() -> AsyncIterator[str]:
        cursor = after
        yield ": connected\n\n"
        try:
            while not await request.is_disconnected():
                async with database.session() as session:
                    # 긴 스트림도 세션 만료와 소속 철회를 계속 확인한다. 쿼리 뒤 풀을 즉시 반환한다.
                    await AuthService(session).authenticate(auth.raw_token)
                    run = await accessible_run(session, auth.user.id, run_id)
                    events = list(
                        (
                            await session.scalars(
                                select(GenerationEvent)
                                .where(
                                    GenerationEvent.generation_id == run_id,
                                    GenerationEvent.sequence > cursor,
                                )
                                .order_by(GenerationEvent.sequence)
                                .limit(100)
                            )
                        ).all()
                    )
                    chunks = [
                        (
                            event.sequence,
                            event.kind,
                            json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")),
                        )
                        for event in events
                    ]
                    terminal, last_sequence = (
                        run.status in TERMINAL_STATUSES,
                        run.last_event_sequence,
                    )
                for sequence, kind, payload in chunks:
                    cursor = sequence
                    yield f"id: {sequence}\nevent: {kind}\ndata: {payload}\n\n"
                if terminal and cursor >= last_sequence:
                    return
                if not chunks:
                    yield ": waiting\n\n"
                    await asyncio.sleep(0.05)
        except (InvalidSession, AccessDenied):
            yield (
                "event: error\ndata: "
                '{"message":"로그인 또는 대화 접근 권한을 다시 확인하세요."}\n\n'
            )
        except (SQLAlchemyError, RepositoryUnavailable):
            # 연결을 닫으면 클라이언트가 마지막 이벤트 번호부터 다시 요청한다.
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
    )
