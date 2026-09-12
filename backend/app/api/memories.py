"""인증된 본인의 기억을 명시적으로 조회·저장·수정·삭제한다."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.auth import CurrentAuth, WriteAuth
from backend.app.api.conversations import (
    EmptyPayload,
    data_session,
    parse_id,
    private_json,
    read_json_payload,
    read_query,
)
from backend.app.services.memories import MemoryService

router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


class MemoryRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    revision: int = Field(ge=0, le=2**53 - 1)


class MemoryPayload(MemoryRevision):
    key: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=500)


@router.get("")
async def list_memories(request: Request, auth: CurrentAuth):
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        return private_json(await MemoryService(session, auth.user.id).snapshot())


@router.post("")
async def create_memory(request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    body = await read_json_payload(request, MemoryPayload)
    async with data_session(request) as session:
        result = await MemoryService(session, auth.user.id).change(**body.model_dump())
        await session.commit()
        return private_json(result, status_code=201)


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    body = await read_json_payload(request, MemoryPayload)
    async with data_session(request) as session:
        result = await MemoryService(session, auth.user.id).change(
            memory_id=parse_id(memory_id), **body.model_dump()
        )
        await session.commit()
        return private_json(result)


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    body = await read_json_payload(request, MemoryRevision)
    async with data_session(request) as session:
        result = await MemoryService(session, auth.user.id).change(
            memory_id=parse_id(memory_id), delete=True, revision=body.revision
        )
        await session.commit()
        return private_json(result)
