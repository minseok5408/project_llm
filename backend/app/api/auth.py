"""로컬 웹 중계 경계를 확인하고 24시간 세션 쿠키로 사용자를 인증한다."""

import hashlib
import hmac
import ipaddress
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models import User
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, RepositoryUnavailable
from backend.app.services.auth import (
    SESSION_DURATION_SECONDS,
    AuthService,
    InvalidCredentials,
    InvalidSession,
    SignupDisabled,
    csrf_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class SignupPayload(LoginPayload):
    display_name: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True, slots=True)
class AuthUser:
    id: UUID
    email: str
    display_name: str
    platform_role: str


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: AuthUser
    expires_at: datetime
    csrf_token: str = field(repr=False)
    raw_token: str = field(repr=False)


class AuthNoStoreMiddleware:
    """인증 성공과 실패 응답을 캐시하지 않되 SSE의 기존 캐시 지시문은 유지한다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_private)


class AuthRateLimiter:
    """한 프로세스에서 IP와 정규화한 계정별 인증 시도를 제한한다."""

    def __init__(self) -> None:
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self.max_keys = 4096

    def check(self, *, ip: str, email: str, operation: str) -> None:
        account = hashlib.sha256(email.strip().lower().encode(errors="replace")).hexdigest()
        now = time.monotonic()
        limits = ((f"ip:{ip}", 30), (f"{operation}:account:{account}", 10))
        for key, limit in limits:
            attempts = self._attempts.setdefault(key, deque())
            self._attempts.move_to_end(key)
            # 이미 차단된 계정을 여러 주소로 반복 요청해도 저장 공간은 즉시 제한한다.
            while len(self._attempts) > self.max_keys:
                self._attempts.popitem(last=False)
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= limit:
                raise HTTPException(
                    status_code=429,
                    detail="요청이 너무 많습니다. 잠시 후 다시 시도하세요.",
                    headers={"Retry-After": "60"},
                )
            attempts.append(now)


def validate_write_origin(request: Request) -> None:
    """웹 서버가 덮어쓴 origin과 브라우저 origin이 같은 로컬 주소인지 검사한다."""
    origins = request.headers.getlist("origin")
    trusted = request.headers.getlist("x-project-llm-origin")
    valid = len(origins) == len(trusted) == 1 and origins[0] == trusted[0]
    if valid:
        try:
            origin = urlsplit(origins[0])
            host = origin.hostname or ""
            valid = (
                origin.scheme in {"http", "https"}
                and not origin.username
                and not origin.password
                and not origin.path
                and not origin.query
                and not origin.fragment
                and (origin.port is None or 1 <= origin.port <= 65535)
            )
            if host != "localhost":
                address = ipaddress.ip_address(host)
                valid = valid and (
                    address.is_loopback or any(address in network for network in _PRIVATE_NETWORKS)
                )
        except ValueError:
            valid = False
    if not valid:
        raise HTTPException(status_code=403, detail="허용되지 않은 요청 출처입니다.")


def require_json(request: Request) -> None:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(status_code=415, detail="JSON 요청이 필요합니다.")


async def read_payload[T: BaseModel](request: Request, model: type[T]) -> T:
    validate_write_origin(request)
    require_json(request)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_384:
            raise HTTPException(status_code=413, detail="인증 요청이 너무 큽니다.")
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError:
        # Pydantic의 기본 오류 응답에는 입력값이 포함될 수 있어 상세 내용을 반환하지 않는다.
        raise HTTPException(status_code=422, detail="인증 입력 형식이 올바르지 않습니다.") from None


@asynccontextmanager
async def auth_service(request: Request) -> AsyncIterator[tuple[AuthService, AsyncSession]]:
    database: Database | None = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="인증 데이터베이스를 사용할 수 없습니다.")
    try:
        async with database.session() as session:
            yield AuthService(session, settings=request.app.state.settings), session
    except (InvalidCredentials, InvalidSession):
        raise HTTPException(status_code=401, detail="로그인 정보가 유효하지 않습니다.") from None
    except SignupDisabled:
        raise HTTPException(status_code=403, detail="현재 회원가입을 받지 않습니다.") from None
    except InvalidInput:
        raise HTTPException(status_code=422, detail="인증 입력 형식이 올바르지 않습니다.") from None
    except Conflict:
        raise HTTPException(status_code=409, detail="가입 요청을 완료할 수 없습니다.") from None
    except AccessDenied:
        raise HTTPException(status_code=403, detail="인증 요청을 처리할 수 없습니다.") from None
    except (SQLAlchemyError, RepositoryUnavailable):
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.") from None


async def require_auth(request: Request) -> AuthContext:
    async with auth_service(request) as (service, _session):
        raw_token = request.cookies.get(request.app.state.settings.auth_cookie_name, "")
        authenticated = await service.authenticate(raw_token)
        user = authenticated.user
        # 읽기 트랜잭션 종료 시 ORM 속성이 만료되므로 응답에 필요한 값만 먼저 복사한다.
        return AuthContext(
            AuthUser(user.id, user.email, user.display_name, user.platform_role),
            authenticated.session.expires_at,
            csrf_token(raw_token),
            raw_token,
        )


CurrentAuth = Annotated[AuthContext, Depends(require_auth)]


async def require_write_auth(request: Request, auth: CurrentAuth) -> AuthContext:
    validate_write_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied or not hmac.compare_digest(supplied.encode(), auth.csrf_token.encode()):
        raise HTTPException(status_code=403, detail="CSRF 검증에 실패했습니다.")
    return auth


WriteAuth = Annotated[AuthContext, Depends(require_write_auth)]


def session_payload(user: User | AuthUser, expires_at: datetime, token: str) -> dict:
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "platform_role": user.platform_role,
        },
        "expires_at": expires_at.isoformat(),
        "csrf_token": csrf_token(token),
    }


def login_response(request: Request, result) -> JSONResponse:
    settings: Settings = request.app.state.settings
    response = JSONResponse(
        session_payload(result.user, result.expires_at, result.raw_token),
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        settings.auth_cookie_name,
        result.raw_token,
        max_age=SESSION_DURATION_SECONDS,
        expires=result.expires_at,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/config")
async def auth_config(request: Request) -> JSONResponse:
    return JSONResponse(
        {"signup_mode": request.app.state.settings.signup_mode, "session_hours": 24},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login")
async def login(request: Request) -> JSONResponse:
    payload = await read_payload(request, LoginPayload)
    request.app.state.auth_rate_limiter.check(
        ip=request.client.host if request.client else "unknown",
        email=payload.email,
        operation="login",
    )
    async with auth_service(request) as (service, session):
        result = await service.login(
            email=payload.email, password=payload.password.get_secret_value()
        )
        await session.commit()
    return login_response(request, result)


@router.post("/signup")
async def signup(request: Request) -> JSONResponse:
    payload = await read_payload(request, SignupPayload)
    request.app.state.auth_rate_limiter.check(
        ip=request.client.host if request.client else "unknown",
        email=payload.email,
        operation="signup",
    )
    async with auth_service(request) as (service, session):
        result = await service.signup(
            email=payload.email,
            password=payload.password.get_secret_value(),
            display_name=payload.display_name,
        )
        await session.commit()
    return login_response(request, result)


@router.get("/me")
async def me(auth: CurrentAuth) -> JSONResponse:
    return JSONResponse(
        session_payload(auth.user, auth.expires_at, auth.raw_token),
        headers={"Cache-Control": "no-store"},
    )


def clear_cookie(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(request: Request, auth: WriteAuth) -> Response:
    async with auth_service(request) as (service, session):
        await service.logout(auth.raw_token)
        await session.commit()
    return clear_cookie(request)
