"""실제 PostgreSQL에서 비밀번호·세션 수명·철회와 기존 계정 보호를 검증한다."""

import asyncio
import hashlib
import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.models import (
    AuthIdentity,
    AuthSession,
    TokenBudget,
    UsagePlan,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    create_user_with_workspace,
)
from backend.app.services import auth
from backend.app.services.auth import (
    SESSION_DURATION_SECONDS,
    AuthService,
    InvalidCredentials,
    InvalidSession,
    SignupDisabled,
    csrf_token,
)
from backend.tests.conftest import database_settings

pytestmark = pytest.mark.postgres
PASSWORD = "test-password-12345"
NEW_PASSWORD = "replacement-password-67890"


def open_settings() -> Settings:
    return Settings(_env_file=None, signup_mode="open", database_enabled=False)


async def signup(database: Database, email: str = "member@example.com"):
    async with database.session() as session:
        result = await AuthService(session, settings=open_settings()).signup(
            email=email, password=PASSWORD, display_name="테스트 사용자"
        )
        await session.commit()
        return result


async def login(database: Database, email: str = "member@example.com", password: str = PASSWORD):
    async with database.session() as session:
        result = await AuthService(session).login(email=email, password=password)
        await session.commit()
        return result


async def assert_invalid(database: Database, raw_token: str) -> None:
    async with database.session() as session:
        with pytest.raises(InvalidSession):
            await AuthService(session).authenticate(raw_token)


async def test_signup_stores_hashes_and_session_expires_after_absolute_24_hours(
    schema_database, monkeypatch
) -> None:
    signed_in = await signup(schema_database, email=" Member@Example.com ")
    assert signed_in.user.email == "member@example.com"
    assert signed_in.user.platform_role == "member"
    assert signed_in.raw_token not in repr(signed_in)
    async with schema_database.session() as session:
        identity = await session.scalar(select(AuthIdentity))
        saved = await session.scalar(select(AuthSession))
        assert identity.password_hash.startswith("$argon2id$")
        assert PASSWORD not in identity.password_hash
        assert saved.token_hash == hashlib.sha256(signed_in.raw_token.encode()).hexdigest()
        assert signed_in.raw_token != saved.token_hash
        assert (saved.expires_at - saved.created_at).total_seconds() == SESSION_DURATION_SECONDS
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert await session.scalar(select(WorkspaceMember.role)) == "owner"

        original_expiry = saved.expires_at
        monkeypatch.setattr(auth, "utc_now", lambda: original_expiry - timedelta(seconds=1))
        result = await AuthService(session).authenticate(signed_in.raw_token)
        assert result.user.id == signed_in.user.id
        assert result.session.expires_at == original_expiry
        monkeypatch.setattr(auth, "utc_now", lambda: original_expiry)
        with pytest.raises(InvalidSession):
            await AuthService(session).authenticate(signed_in.raw_token)
        assert saved.expires_at == original_expiry


async def test_login_errors_do_not_reveal_missing_disabled_or_wrong_password_accounts(
    schema_database, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.repositories.core")
    first = await signup(schema_database)
    errors = []
    for email, password in [
        ("member@example.com", "incorrect-password-123"),
        ("missing@example.com", PASSWORD),
    ]:
        async with schema_database.session() as session:
            with pytest.raises(InvalidCredentials) as caught:
                await AuthService(session).login(email=email, password=password)
            errors.append(str(caught.value))
    async with schema_database.session() as session:
        user = await session.get(User, first.user.id)
        user.status = "disabled"
        await session.commit()
    async with schema_database.session() as session:
        with pytest.raises(InvalidCredentials) as caught:
            await AuthService(session).login(email="member@example.com", password=PASSWORD)
        errors.append(str(caught.value))
    assert len(set(errors)) == 1
    await assert_invalid(schema_database, first.raw_token)
    assert PASSWORD not in caplog.text
    assert first.raw_token not in caplog.text
    assert "member@example.com" not in caplog.text


async def test_logout_revokes_only_the_current_session_and_preserves_other_sessions(
    schema_database,
):
    first = await signup(schema_database)
    second = await login(schema_database)
    assert first.raw_token != second.raw_token
    assert csrf_token(first.raw_token) == csrf_token(first.raw_token)
    assert csrf_token(first.raw_token) != csrf_token(second.raw_token)
    assert csrf_token(first.raw_token) != first.raw_token
    async with schema_database.session() as session:
        await AuthService(session).logout(first.raw_token)
        await AuthService(session).logout(first.raw_token)
        await AuthService(session).logout("invalid-token")
        await session.commit()
    await assert_invalid(schema_database, first.raw_token)
    third = await login(schema_database)
    async with schema_database.session() as session:
        await AuthService(session).authenticate(second.raw_token)
        await AuthService(session).logout(second.raw_token)
        await session.commit()
    await assert_invalid(schema_database, second.raw_token)
    async with schema_database.session() as session:
        await AuthService(session).authenticate(third.raw_token)


async def test_password_setup_preserves_system_role_and_signup_cannot_take_existing_account(
    schema_database,
) -> None:
    async with schema_database.session() as session:
        account = await create_user_with_workspace(
            session, email="system@example.com", display_name="system"
        )
        account.user.platform_role = "system"
        user = await AuthService(session).set_password(
            email="system@example.com", password=PASSWORD
        )
        assert user.id == account.user.id
        await session.commit()
    original = await login(schema_database, email="system@example.com")
    async with schema_database.session() as session:
        with pytest.raises(Conflict):
            await AuthService(session, settings=open_settings()).signup(
                email="SYSTEM@example.com", password=NEW_PASSWORD, display_name="인수 시도"
            )
    async with schema_database.session() as session:
        user = await AuthService(session).set_password(
            email="system@example.com", password=NEW_PASSWORD
        )
        assert user.platform_role == "system"
        await session.commit()
    await assert_invalid(schema_database, original.raw_token)
    async with schema_database.session() as session:
        with pytest.raises(InvalidCredentials):
            await AuthService(session).login(email="system@example.com", password=PASSWORD)
    changed = await login(schema_database, email="system@example.com", password=NEW_PASSWORD)
    assert changed.user.id == original.user.id
    assert changed.user.platform_role == "system"
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(AuthIdentity)) == 1
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 1


async def test_signup_disabled_and_password_setup_do_not_create_or_enable_accounts(schema_database):
    async with schema_database.session() as session:
        disabled_settings = Settings(_env_file=None, signup_mode="disabled", database_enabled=False)
        with pytest.raises(SignupDisabled):
            await AuthService(session, settings=disabled_settings).signup(
                email="new@example.com", password=PASSWORD, display_name="신규"
            )
        for password in ("x" * 7, "x" * 33):
            with pytest.raises(InvalidInput):
                await AuthService(session, settings=open_settings()).signup(
                    email="new@example.com", password=password, display_name="신규"
                )
        with pytest.raises(AccessDenied):
            await AuthService(session).set_password(email="missing@example.com", password=PASSWORD)
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        account = await create_user_with_workspace(
            session, email="disabled@example.com", display_name="비활성"
        )
        account.user.status = "disabled"
        await session.commit()
    async with schema_database.session() as session:
        with pytest.raises(AccessDenied):
            await AuthService(session).set_password(email="disabled@example.com", password=PASSWORD)
        assert await session.scalar(select(User.status)) == "disabled"
        assert await session.scalar(select(func.count()).select_from(AuthIdentity)) == 0


async def test_signup_and_password_change_are_rolled_back_with_the_callers_transaction(
    schema_database,
) -> None:
    async with schema_database.session() as session:
        await AuthService(session, settings=open_settings()).signup(
            email="rollback@example.com", password=PASSWORD, display_name="롤백"
        )
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(AuthIdentity)) == 0
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert await session.scalar(select(func.count()).select_from(TokenBudget)) == 0
        assert await session.scalar(select(func.count()).select_from(UsagePlan)) == 0
    first = await signup(schema_database)
    async with schema_database.session() as session:
        await AuthService(session).set_password(email="member@example.com", password=NEW_PASSWORD)
    async with schema_database.session() as session:
        await AuthService(session).authenticate(first.raw_token)
    await login(schema_database)


async def test_database_rejects_plain_credentials_and_session_lifetime_extension(schema_database):
    await signup(schema_database)
    for statement in [
        "UPDATE auth_identities SET password_hash='plain-password'",
        "UPDATE auth_sessions SET token_hash='plain-token'",
        "UPDATE auth_sessions SET expires_at=expires_at+interval '1 second'",
    ]:
        async with schema_database.session() as session:
            with pytest.raises(IntegrityError):
                await session.execute(text(statement))


async def test_authentication_preserves_pending_account_disable_and_session_revocation(
    schema_database,
) -> None:
    signed_in = await signup(schema_database)
    async with schema_database.session() as session:
        user = await session.get(User, signed_in.user.id)
        user.status = "disabled"
        with pytest.raises(InvalidSession):
            await AuthService(session).authenticate(signed_in.raw_token)
        assert user.status == "disabled"
    async with schema_database.session() as session:
        saved = await session.scalar(select(AuthSession))
        revoked_at = auth.utc_now()
        saved.revoked_at = revoked_at
        with pytest.raises(InvalidSession):
            await AuthService(session).authenticate(signed_in.raw_token)
        assert saved.revoked_at == revoked_at


async def test_password_change_during_login_rejects_the_previously_verified_password(
    schema_database, postgres, monkeypatch
) -> None:
    first = await signup(schema_database)
    other_database = Database(database_settings(postgres))
    verify_started = asyncio.Event()
    continue_verify = asyncio.Event()
    original_to_thread = asyncio.to_thread

    async def delayed_verify(function, *args, **kwargs):
        if function is auth._verify_password:
            verify_started.set()
            await continue_verify.wait()
        return await original_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", delayed_verify)
    task = asyncio.create_task(login(schema_database))
    try:
        await asyncio.wait_for(verify_started.wait(), timeout=5)
        async with other_database.session() as session:
            await AuthService(session).set_password(
                email="member@example.com", password=NEW_PASSWORD
            )
            await session.commit()
        continue_verify.set()
        with pytest.raises(InvalidCredentials):
            await asyncio.wait_for(task, timeout=5)
        await assert_invalid(schema_database, first.raw_token)
        async with schema_database.session() as session:
            assert await session.scalar(select(func.count()).select_from(AuthSession)) == 1
    finally:
        continue_verify.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await other_database.dispose()
