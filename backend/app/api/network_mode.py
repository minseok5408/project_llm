"""로그인한 사용자의 로컬 전용 설정과 검색 연결 상태를 제공한다."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.auth import CurrentAuth, WriteAuth
from backend.app.api.conversations import EmptyPayload, private_json, read_json_payload, read_query
from backend.app.repositories import AccessDenied, RepositoryUnavailable
from backend.app.services.network_mode import NetworkModeService

router = APIRouter(prefix="/api/v1/network-mode", tags=["network-mode"])


class NetworkModePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    local_only: bool


def service(request: Request) -> NetworkModeService:
    result = getattr(request.app.state, "network_mode", None)
    if result is None:
        raise HTTPException(status_code=503, detail="네트워크 설정 저장소를 사용할 수 없습니다.")
    return result


@asynccontextmanager
async def network_errors():
    try:
        yield
    except AccessDenied:
        raise HTTPException(status_code=403, detail="네트워크 설정에 접근할 수 없습니다.") from None
    except (RepositoryUnavailable, SQLAlchemyError):
        raise HTTPException(status_code=503, detail="네트워크 설정을 확인할 수 없습니다.") from None


@router.get("")
async def get_mode(request: Request, auth: CurrentAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    async with network_errors():
        payload = await service(request).status(auth.user.id)
    return private_json(payload)


@router.patch("")
async def change_mode(request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    body = await read_json_payload(request, NetworkModePayload)
    async with network_errors():
        payload = await service(request).set_local_only(auth.user.id, body.local_only)
    return private_json(payload)


@router.post("/check")
async def check_mode(request: Request, auth: WriteAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    await read_json_payload(request, EmptyPayload)
    async with network_errors():
        payload = await service(request).status(auth.user.id, force=True)
    return private_json(payload)
