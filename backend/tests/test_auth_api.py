"""HTTP 인증 경계, 쿠키, CSRF와 실제 PostgreSQL 세션의 통합 검증."""

import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, update

from backend.app.api.auth import AuthRateLimiter
from backend.app.config import Settings
from backend.app.db import Database
from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app
from backend.app.models import AuthSession, User
from backend.app.repositories import create_user_with_workspace
from backend.app.services.auth import AuthService
from backend.tests.conftest import IsolatedPostgres, database_settings

ORIGIN = "http://192.168.1.20:3000"
WRITE_HEADERS = {"Origin": ORIGIN, "X-Project-LLM-Origin": ORIGIN}
PASSWORD = "Safe-Test-Password-123!"
SIGNUP = {"email": "member@example.com", "display_name": "일반 사용자", "password": PASSWORD}
CHAT = {"messages": [{"role": "user", "content": "한글 대화"}]}


@pytest.fixture
async def closed_client() -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(_env_file=None, database_enabled=False, llm_backend="mock")
    app = create_app(settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000"
        ) as client,
    ):
        yield client


async def test_auth_database_failure_is_closed_and_health_and_config_remain_public(closed_client):
    for path in ("/health/live", "/health/ready", "/api/v1/auth/config"):
        assert (await closed_client.get(path)).status_code == 200
    config = await closed_client.get("/api/v1/auth/config")
    assert config.json() == {"signup_mode": "open", "session_hours": 24}
    for path in ("/api/v1/auth/me", "/api/status"):
        response = await closed_client.get(path)
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
    response = await closed_client.post(
        "/api/v1/auth/login",
        json={"email": SIGNUP["email"], "password": PASSWORD},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 503


async def test_auth_rejects_untrusted_origins_and_never_echoes_invalid_password(
    closed_client, caplog
):
    for headers in [
        {},
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-Project-LLM-Origin": "http://192.168.1.30:3000"},
        {"Origin": "https://outside.example", "X-Project-LLM-Origin": "https://outside.example"},
        {"Origin": "http://169.254.1.2:3000", "X-Project-LLM-Origin": "http://169.254.1.2:3000"},
    ]:
        response = await closed_client.post("/api/v1/auth/login", json=SIGNUP, headers=headers)
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
    response = await closed_client.post(
        "/api/v1/auth/login", content="password=private", headers=WRITE_HEADERS
    )
    assert response.status_code == 415
    private = "private-password-must-not-appear"
    with caplog.at_level(logging.INFO):
        response = await closed_client.post(
            "/api/v1/auth/login",
            headers=WRITE_HEADERS,
            json={"email": "member@example.com", "password": {"secret": private}},
        )
    assert response.status_code == 422
    assert private not in response.text + caplog.text
    assert "input" not in response.text


async def test_auth_rate_limit_is_per_account_per_ip_and_memory_bounded():
    limiter = AuthRateLimiter()
    for _ in range(10):
        limiter.check(ip="127.0.0.1", email=" Member@Example.com ", operation="login")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as blocked:
        limiter.check(ip="127.0.0.2", email="member@example.com", operation="login")
    assert blocked.value.status_code == 429
    assert blocked.value.headers["Retry-After"] == "60"
    limiter = AuthRateLimiter()
    for index in range(30):
        limiter.check(ip="127.0.0.1", email=f"account{index}@example.com", operation="login")
    with pytest.raises(HTTPException) as blocked:
        limiter.check(ip="127.0.0.1", email="another@example.com", operation="signup")
    assert blocked.value.status_code == 429
    for index in range(5000):
        limiter.check(ip=f"client-{index}", email=f"memory{index}@example.com", operation="login")
    assert len(limiter._attempts) <= limiter.max_keys
    limiter = AuthRateLimiter()
    limiter.max_keys = 64
    for _ in range(10):
        limiter.check(ip="initial", email="blocked@example.com", operation="login")
    for index in range(128):
        with pytest.raises(HTTPException):
            limiter.check(ip=f"rejected-{index}", email="blocked@example.com", operation="login")
    assert len(limiter._attempts) <= limiter.max_keys


@pytest.fixture
async def auth_client(schema_database: Database, postgres: IsolatedPostgres):
    settings = database_settings(postgres).model_copy(update={"signup_mode": "open"})
    app = create_app(settings=settings, provider=MockProvider(settings, delay_seconds=0))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8000",
            headers=WRITE_HEADERS,
        ) as client,
    ):
        yield client, app


@pytest.mark.postgres
async def test_signup_sets_absolute_24h_cookie_and_me_does_not_extend_it(
    auth_client, schema_database
):
    client, app = auth_client
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/status")).status_code == 401
    assert (await client.post("/api/chat", json=CHAT)).status_code == 401
    signup = await client.post("/api/v1/auth/signup", json=SIGNUP)
    assert signup.status_code == 200
    payload = signup.json()
    assert payload["user"]["platform_role"] == "member"
    assert set(payload["user"]) == {"id", "email", "display_name", "platform_role"}
    cookie = SimpleCookie(signup.headers["set-cookie"])["project_llm_session"]
    assert cookie["httponly"] is True
    assert cookie["secure"] == ""
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == "/"
    assert cookie["max-age"] == "86400"
    assert cookie["domain"] == ""
    expires_at = datetime.fromisoformat(payload["expires_at"])
    assert abs((parsedate_to_datetime(cookie["expires"]) - expires_at).total_seconds()) < 1
    assert PASSWORD not in signup.text + signup.headers["set-cookie"]
    async with schema_database.session() as session:
        saved = (await session.scalars(select(AuthSession))).one()
        assert saved.expires_at - saved.created_at == timedelta(hours=24)
        assert saved.token_hash == hashlib.sha256(cookie.value.encode()).hexdigest()
        assert saved.token_hash != cookie.value
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == payload
    assert "set-cookie" not in me.headers
    assert (await client.get("/api/status")).status_code == 200
    assert app.state.database.engine.pool.checkedout() == 0


@pytest.mark.postgres
@pytest.mark.parametrize("length, expected_status", [(7, 422), (8, 200), (32, 200), (33, 422)])
async def test_signup_password_length_and_login_boundaries(
    auth_client, schema_database, length, expected_status
):
    client, _ = auth_client
    password = "x" * length
    response = await client.post("/api/v1/auth/signup", json={**SIGNUP, "password": password})
    assert response.status_code == expected_status
    assert password not in response.text
    if expected_status == 422:
        assert "set-cookie" not in response.headers
        async with schema_database.session() as session:
            assert await session.scalar(select(User).where(User.email == SIGNUP["email"])) is None
        return
    payload = response.json()
    assert payload["user"]["display_name"] == SIGNUP["display_name"]
    assert payload["user"]["platform_role"] == "member"
    workspaces = (await client.get("/api/v1/workspaces")).json()
    assert len(workspaces["items"]) == 1
    usage = (await client.get("/api/v1/usage")).json()
    assert usage["remaining_tokens"] == 20_000
    assert usage["budget_source"] == "free_monthly"
    assert usage["unlimited"] is False
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": payload["csrf_token"]}, json={}
        )
    ).status_code == 204
    signed_in = await client.post(
        "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": password}
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["user"] == payload["user"]


@pytest.mark.postgres
@pytest.mark.parametrize("length", [7, 33])
async def test_login_rejects_password_outside_signup_policy(auth_client, length):
    client, _ = auth_client
    assert (await client.post("/api/v1/auth/signup", json=SIGNUP)).status_code == 200
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": "x" * length}
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.postgres
async def test_signup_cannot_inject_system_role_and_disabled_signup_is_enforced(auth_client):
    client, app = auth_client
    rejected = await client.post("/api/v1/auth/signup", json={**SIGNUP, "platform_role": "system"})
    assert rejected.status_code == 422
    app.state.settings.signup_mode = "disabled"
    rejected = await client.post("/api/v1/auth/signup", json=SIGNUP)
    assert rejected.status_code == 403
    assert "set-cookie" not in rejected.headers


@pytest.mark.postgres
async def test_login_errors_are_generic_and_rate_limited(auth_client, caplog):
    client, _ = auth_client
    assert (await client.post("/api/v1/auth/signup", json=SIGNUP)).status_code == 200
    wrong_password = "Wrong-Secret-Password-123!"
    with caplog.at_level(logging.INFO):
        wrong = await client.post(
            "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": wrong_password}
        )
        missing = await client.post(
            "/api/v1/auth/login", json={"email": "missing@example.com", "password": wrong_password}
        )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()
    assert wrong_password not in wrong.text + caplog.text
    for _ in range(9):
        assert (
            await client.post(
                "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": "wrong"}
            )
        ).status_code == 401
    blocked = await client.post(
        "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": "wrong"}
    )
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"
    assert blocked.headers["cache-control"] == "no-store"


@pytest.mark.postgres
async def test_csrf_is_bound_to_current_session_and_logout_all_revokes_other_devices(auth_client):
    client, app = auth_client
    signup = await client.post("/api/v1/auth/signup", json=SIGNUP)
    csrf = signup.json()["csrf_token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000", headers=WRITE_HEADERS
    ) as other:
        login = await other.post(
            "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": PASSWORD}
        )
        assert login.status_code == 200
        other_csrf = login.json()["csrf_token"]
        assert other_csrf != csrf
        for headers in ({}, {"X-CSRF-Token": other_csrf}):
            assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 403
        logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert logout.status_code == 204
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await other.get("/api/v1/auth/me")).status_code == 200
        login = await client.post(
            "/api/v1/auth/login", json={"email": SIGNUP["email"], "password": PASSWORD}
        )
        logout_all = await client.post(
            "/api/v1/auth/logout-all", headers={"X-CSRF-Token": login.json()["csrf_token"]}
        )
        assert logout_all.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await other.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.postgres
@pytest.mark.parametrize("invalid_state", ["expired", "disabled"])
async def test_expired_sessions_and_disabled_users_are_unauthenticated(
    auth_client, schema_database, invalid_state
):
    client, _ = auth_client
    signup = await client.post("/api/v1/auth/signup", json=SIGNUP)
    user_id = UUID(signup.json()["user"]["id"])
    async with schema_database.engine.begin() as connection:
        if invalid_state == "expired":
            ended = datetime.now(UTC) - timedelta(seconds=1)
            await connection.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id)
                .values(
                    created_at=ended - timedelta(hours=24),
                    expires_at=ended,
                )
            )
        else:
            await connection.execute(
                update(User).where(User.id == user_id).values(status="disabled")
            )
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/status")).status_code == 401


@pytest.mark.postgres
async def test_legacy_chat_is_gone_for_both_roles_and_still_requires_csrf(
    auth_client, schema_database
):
    client, app = auth_client
    signup = await client.post("/api/v1/auth/signup", json=SIGNUP)
    csrf = signup.json()["csrf_token"]
    assert (await client.post("/api/chat", json=CHAT)).status_code == 403
    denied = await client.post("/api/chat", json=CHAT, headers={"X-CSRF-Token": csrf})
    assert denied.status_code == 410
    assert "저장형" in denied.json()["detail"]
    async with schema_database.session() as session:
        system = await create_user_with_workspace(
            session, email="system@example.com", display_name="관리자"
        )
        system.user.platform_role = "system"
        await session.flush()
        await AuthService(session, settings=app.state.settings).set_password(
            email="system@example.com", password=PASSWORD
        )
        await session.commit()
    login = await client.post(
        "/api/v1/auth/login", json={"email": "system@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["user"]["platform_role"] == "system"
    assert (await client.post("/api/chat", json=CHAT)).status_code == 403
    result = await client.post(
        "/api/chat", json=CHAT, headers={"X-CSRF-Token": login.json()["csrf_token"]}
    )
    assert result.status_code == 410
    assert app.state.database.engine.pool.checkedout() == 0


@pytest.mark.postgres
async def test_secure_mode_uses_host_cookie_prefix(auth_client):
    client, app = auth_client
    app.state.settings.auth_cookie_secure = True
    signup = await client.post("/api/v1/auth/signup", json=SIGNUP)
    assert signup.status_code == 200
    cookie = SimpleCookie(signup.headers["set-cookie"])["__Host-session"]
    assert cookie["secure"] is True
    assert cookie["httponly"] is True
    assert cookie["path"] == "/"
    assert cookie["domain"] == ""
