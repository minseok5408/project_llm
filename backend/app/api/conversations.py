"""인증된 작업 공간 소속을 기준으로 대화와 메시지를 조회하고 관리한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.auth import CurrentAuth, WriteAuth, require_json
from backend.app.db import Database
from backend.app.models import Conversation, GenerationEvent, GenerationRun, Message, WebSearchRun
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    Repository,
    RepositoryUnavailable,
)
from backend.app.tools.web_search.service import search_payload

router = APIRouter(prefix="/api/v1", tags=["conversations"])


class CreateConversationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    workspace_id: UUID
    title: str = Field(default="새 대화", min_length=1, max_length=300)


class UpdateConversationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    is_pinned: bool | None = None
    status: Literal["active", "archived"] | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("변경할 값을 지정해야 합니다.")
        return self


class EmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ConversationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    workspace_id: UUID
    status: Literal["active", "archived", "all"] = "active"
    q: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)
    limit: int = Field(default=30, ge=1, le=100)

    @field_validator("q", mode="before")
    @classmethod
    def trim_search_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class MessageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    before: int | None = Field(default=None, ge=1, le=2**63 - 1)
    limit: int = Field(default=50, ge=1, le=100)


async def read_json_payload[T: BaseModel](request: Request, model: type[T]) -> T:
    """크기를 제한하고 검증 오류에 요청 원문이나 필드 값을 포함하지 않는다."""
    require_json(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16384:
            raise HTTPException(status_code=413, detail="요청 본문이 너무 큽니다.")
    try:
        return model.model_validate_json(bytes(data))
    except ValidationError:
        raise HTTPException(status_code=422, detail="요청 형식이 올바르지 않습니다.") from None


def read_query[T: BaseModel](request: Request, model: type[T]) -> T:
    try:
        if len(request.query_params.multi_items()) != len(request.query_params):
            raise ValueError
        return model.model_validate(dict(request.query_params))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="조회 조건이 올바르지 않습니다.") from None


def parse_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="식별자가 올바르지 않습니다.") from None


def private_json(payload: object, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(payload),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@asynccontextmanager
async def data_session(request: Request) -> AsyncIterator[AsyncSession]:
    """세션 종료 전 응답 값을 복사하고 권한 밖 대상과 DB 예외를 안전하게 감춘다."""
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="데이터 저장소를 사용할 수 없습니다.")
    try:
        async with database.session() as session:
            yield session
    except AccessDenied:
        raise HTTPException(status_code=404, detail="대상을 찾을 수 없습니다.") from None
    except InvalidInput:
        raise HTTPException(status_code=422, detail="요청 값이 올바르지 않습니다.") from None
    except (Conflict, IntegrityError):
        raise HTTPException(
            status_code=409, detail="현재 상태에서 작업을 완료할 수 없습니다."
        ) from None
    except (RepositoryUnavailable, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="데이터 저장소를 사용할 수 없습니다.") from None


def conversation_payload(conversation: Conversation, active_id: UUID | None = None) -> dict:
    return {
        "id": conversation.id,
        "workspace_id": conversation.workspace_id,
        "title": conversation.title,
        "status": conversation.status,
        "is_pinned": conversation.is_pinned,
        "model": conversation.model,
        "last_message_at": conversation.last_message_at,
        "created_at": conversation.created_at,
        "active_generation_id": active_id,
    }


def message_payload(message: Message) -> dict:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "sequence": message.sequence,
        "token_count": message.token_count,
        "created_at": message.created_at,
    }


@router.get("/workspaces")
async def list_workspaces(request: Request, auth: CurrentAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        rows = await Repository(session, auth.user.id).list_workspaces_with_roles()
        return private_json(
            {"items": [{"id": item.id, "name": item.name, "role": role} for item, role in rows]}
        )


@router.get("/conversations")
async def list_conversations(request: Request, auth: CurrentAuth) -> JSONResponse:
    query = read_query(request, ConversationQuery)
    async with data_session(request) as session:
        repository = Repository(session, auth.user.id)
        page = await repository.list_conversations(**query.model_dump())
        active = await repository.active_generation_ids([item.id for item in page.items])
        return private_json(
            {
                "items": [conversation_payload(item, active.get(item.id)) for item in page.items],
                "next_cursor": page.next_cursor,
            }
        )


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    payload = await read_json_payload(request, CreateConversationPayload)
    async with data_session(request) as session:
        conversation = await Repository(session, auth.user.id).create_conversation(
            payload.workspace_id,
            title=payload.title,
            model=request.app.state.settings.llm_model_id,
        )
        response = private_json(conversation_payload(conversation), status_code=201)
        await session.commit()
        return response


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, request: Request, auth: CurrentAuth
) -> JSONResponse:
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        repository = Repository(session, auth.user.id)
        conversation = await repository.get_conversation_by_id(parse_id(conversation_id))
        active = await repository.active_generation_ids([conversation.id])
        return private_json(conversation_payload(conversation, active.get(conversation.id)))


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str, request: Request, auth: WriteAuth
) -> JSONResponse:
    read_query(request, EmptyPayload)
    payload = await read_json_payload(request, UpdateConversationPayload)
    async with data_session(request) as session:
        repository = Repository(session, auth.user.id)
        conversation = await repository.get_conversation_by_id(parse_id(conversation_id))
        conversation = await repository.update_conversation(
            conversation.workspace_id, conversation.id, **payload.model_dump(exclude_unset=True)
        )
        active = await repository.active_generation_ids([conversation.id])
        response = private_json(conversation_payload(conversation, active.get(conversation.id)))
        await session.commit()
        return response


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request, auth: WriteAuth) -> Response:
    read_query(request, EmptyPayload)
    await read_json_payload(request, EmptyPayload)
    async with data_session(request) as session:
        repository = Repository(session, auth.user.id)
        conversation = await repository.get_conversation_by_id(parse_id(conversation_id))
        await repository.soft_delete_conversation(conversation.workspace_id, conversation.id)
        await session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, request: Request, auth: CurrentAuth) -> JSONResponse:
    query = read_query(request, MessageQuery)
    async with data_session(request) as session:
        repository = Repository(session, auth.user.id)
        conversation = await repository.get_conversation_by_id(parse_id(conversation_id))
        page = await repository.list_messages(
            conversation.workspace_id, conversation.id, **query.model_dump()
        )
        active = await repository.active_generation_ids([conversation.id])
        runs = list(
            (
                await session.scalars(
                    select(GenerationRun).where(
                        GenerationRun.conversation_id == conversation.id,
                        GenerationRun.assistant_message_id.in_([item.id for item in page.items]),
                    )
                )
            ).all()
        )
        search_rows = (
            await session.scalars(
                select(WebSearchRun).where(WebSearchRun.generation_id.in_([run.id for run in runs]))
            )
        ).all()
        searches = {row.generation_id: search_payload(row) for row in search_rows}
        latest_user_id = await session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation.id, Message.role == "user")
            .order_by(Message.sequence.desc())
            .limit(1)
        )
        done_events = (
            await session.scalars(
                select(GenerationEvent)
                .where(
                    GenerationEvent.generation_id.in_([run.id for run in runs]),
                    GenerationEvent.kind == "done",
                )
                .order_by(GenerationEvent.sequence.desc())
            )
        ).all()
        reasons = {}
        for event in done_events:
            reason = event.payload.get("finish_reason")
            reasons.setdefault(
                event.generation_id, reason if reason in ("stop", "length") else None
            )
        metadata = {
            run.assistant_message_id: {
                "generation_id": run.id,
                "generation_status": run.status,
                "is_current": run.is_current,
                "can_regenerate": run.is_current
                and run.user_id == auth.user.id
                and run.user_message_id == latest_user_id
                and conversation.status == "active"
                and run.status in ("completed", "failed", "cancelled", "usage_pending")
                and not active.get(conversation.id),
                "finish_reason": reasons.get(run.id),
                "search": searches.get(run.id),
            }
            for run in runs
        }
        return private_json(
            {
                "items": [
                    {**message_payload(item), **metadata.get(item.id, {})} for item in page.items
                ],
                "next_cursor": page.next_cursor,
                "active_generation_id": active.get(conversation.id),
            }
        )
