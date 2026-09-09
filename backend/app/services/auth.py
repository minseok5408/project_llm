"""비밀번호 인증과 절대 24시간 세션. 커밋과 실패 후 롤백은 호출자가 담당한다."""

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings, get_settings
from backend.app.models import AuthIdentity, AuthSession, User
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    create_user_with_workspace,
)
from backend.app.repositories.core import logged, required_text
from backend.app.services.monthly_allowance import MonthlyAllowanceService

SESSION_DURATION_SECONDS = 86_400
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 32
PASSWORD_HASHER = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=65_536, parallelism=4)
# 실제 계정과 무관한 공개 예제 해시로 존재하지 않는 계정에도 같은 검증 연산을 수행한다.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$MIIRqgvgQbgj220jfp0MPA$"
    "YfwJSVjtjSU0zzV/P3S9nnQ/USre2wvJMjfCIjrTQbg"
)


class InvalidCredentials(AccessDenied):
    """이메일·비밀번호 오류와 계정 비활성화를 외부에서 구분하지 않는 로그인 실패."""


class InvalidSession(AccessDenied):
    """누락·만료·철회된 세션 또는 비활성 사용자에게 다시 로그인을 요구한다."""


class SignupDisabled(AccessDenied):
    """운영 설정에서 일반 회원가입을 허용하지 않는다."""


@dataclass(frozen=True, slots=True)
class LoginResult:
    """원문 토큰은 응답 쿠키에만 사용하고 객체 출력에서도 숨긴다."""

    user: User
    raw_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """현재 유효한 세션과 사용자. 세션 조회는 만료 시각을 연장하지 않는다."""

    user: User
    session: AuthSession


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalized_email(value: str) -> str:
    email = required_text(value, 320, "이메일").lower()
    if len(email) > 320 or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) is None:
        raise InvalidInput("이메일 형식이 올바르지 않습니다.")
    return email


def validate_password(value: str) -> str:
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        raise InvalidInput(
            f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상 {PASSWORD_MAX_LENGTH}자 이하여야 합니다."
        )
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidInput("비밀번호에 사용할 수 없는 문자가 있습니다.") from None
    return value


def token_hash(raw_token: str) -> str:
    if not isinstance(raw_token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", raw_token) is None:
        raise InvalidSession("다시 로그인해주세요.")
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def csrf_token(raw_token: str) -> str:
    """세션 원문과 용도를 분리한 CSRF 값을 반환하며 별도 저장은 하지 않는다."""
    token_hash(raw_token)
    return hashlib.sha256(("csrf:" + raw_token).encode("ascii")).hexdigest()


def _verify_password(encoded_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded_hash, password)
    except (InvalidHashError, VerificationError):
        return False


class AuthService:
    """비밀번호 연산은 별도 스레드에서 수행하고 사용자 잠금은 최종 변경 때만 획득한다."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def _issue_session(self, user: User) -> LoginResult:
        now = utc_now()
        raw_token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(seconds=SESSION_DURATION_SECONDS)
        self.session.add(
            AuthSession(
                user_id=user.id,
                token_hash=token_hash(raw_token),
                created_at=now,
                expires_at=expires_at,
            )
        )
        user.last_login_at = now
        await self.session.flush()
        return LoginResult(user=user, raw_token=raw_token, expires_at=expires_at)

    @logged
    async def signup(self, *, email: str, password: str, display_name: str) -> LoginResult:
        if self.settings.signup_mode != "open":
            raise SignupDisabled("현재 회원가입을 받지 않습니다.")
        email = normalized_email(email)
        display_name = required_text(display_name, 200, "표시 이름")
        password = validate_password(password)
        encoded_hash = await asyncio.to_thread(PASSWORD_HASHER.hash, password)
        await self.session.flush()
        try:
            # 기존 계정을 인수하거나 승격하는 경로가 없으며 일반 계정만 새로 생성한다.
            account = await create_user_with_workspace(
                self.session, email=email, display_name=display_name
            )
        except Conflict:
            raise Conflict("회원가입을 완료할 수 없습니다.") from None
        self.session.add(AuthIdentity(user_id=account.user.id, password_hash=encoded_hash))
        await self.session.flush()
        await MonthlyAllowanceService(self.session, account.user.id).ensure()
        return await self._issue_session(account.user)

    @logged
    async def login(self, *, email: str, password: str) -> LoginResult:
        try:
            email, password = normalized_email(email), validate_password(password)
        except InvalidInput:
            raise InvalidCredentials("이메일 또는 비밀번호를 확인해주세요.") from None
        await self.session.flush()
        row = (
            await self.session.execute(
                select(User.id, AuthIdentity.password_hash)
                .join(AuthIdentity, AuthIdentity.user_id == User.id)
                .where(User.email == email, AuthIdentity.provider == "password")
            )
        ).first()
        encoded_hash = row.password_hash if row is not None else _DUMMY_PASSWORD_HASH
        verified = await asyncio.to_thread(_verify_password, encoded_hash, password)
        if not verified or row is None:
            raise InvalidCredentials("이메일 또는 비밀번호를 확인해주세요.")
        replacement_hash = None
        if PASSWORD_HASHER.check_needs_rehash(encoded_hash):
            replacement_hash = await asyncio.to_thread(PASSWORD_HASHER.hash, password)

        # 비밀번호 변경과 같은 사용자 잠금 순서를 사용해 이전 비밀번호로 세션이 발급되지 않게 한다.
        user = await self.session.scalar(
            select(User)
            .where(User.id == row.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        identity = await self.session.scalar(
            select(AuthIdentity)
            .where(AuthIdentity.user_id == row.id, AuthIdentity.provider == "password")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            user is None
            or user.status != "active"
            or identity is None
            or identity.password_hash != encoded_hash
        ):
            raise InvalidCredentials("이메일 또는 비밀번호를 확인해주세요.")
        if replacement_hash is not None:
            identity.password_hash = replacement_hash
        return await self._issue_session(user)

    @logged
    async def authenticate(self, raw_token: str) -> AuthenticatedSession:
        digest = token_hash(raw_token)
        # 재조회가 호출자의 미반영 비활성화·세션 철회 상태를 덮어쓰지 않게 한다.
        await self.session.flush()
        now = utc_now()
        row = (
            await self.session.execute(
                select(User, AuthSession)
                .join(AuthSession, AuthSession.user_id == User.id)
                .where(
                    AuthSession.token_hash == digest,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    User.status == "active",
                )
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None or row.AuthSession.expires_at <= utc_now():
            raise InvalidSession("다시 로그인해주세요.")
        return AuthenticatedSession(user=row.User, session=row.AuthSession)

    @logged
    async def logout(self, raw_token: str) -> None:
        try:
            digest = token_hash(raw_token)
        except InvalidSession:
            return
        await self.session.flush()
        await self.session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.session.flush()

    async def _revoke_user_sessions(self, user_id: UUID) -> None:
        await self.session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.session.flush()

    @logged
    async def set_password(self, *, email: str, password: str) -> User:
        """명시적 로컬 관리 명령용이며 기존 계정에만 비밀번호를 설정하고 세션을 전부 철회한다."""
        email, password = normalized_email(email), validate_password(password)
        encoded_hash = await asyncio.to_thread(PASSWORD_HASHER.hash, password)
        # autoflush가 꺼져 있어 역할·상태 등 호출자의 변경을 잠금 재조회 전에 보존한다.
        await self.session.flush()
        user = await self.session.scalar(
            select(User)
            .where(User.email == email)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or user.status != "active":
            raise AccessDenied("비밀번호를 설정할 수 있는 계정을 찾지 못했습니다.")
        identity = await self.session.scalar(
            select(AuthIdentity)
            .where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "password")
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if identity is None:
            self.session.add(AuthIdentity(user_id=user.id, password_hash=encoded_hash))
        else:
            identity.password_hash = encoded_hash
        await self._revoke_user_sessions(user.id)
        return user
