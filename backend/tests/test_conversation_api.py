"""실제 PostgreSQL에서 작업 공간 권한, 대화 복원과 사용량 HTTP 계약을 검증한다."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from backend.app.db import Database
from backend.app.main import create_app
from backend.app.models import (
    Conversation,
    GenerationRun,
    TokenBudget,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.providers import MockProvider
from backend.app.repositories import Repository
from backend.app.services.auth import AuthService, csrf_token
from backend.app.services.token_quota import TokenQuotaService
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = pytest.mark.postgres
ORIGIN = "http://192.168.1.20:3000"
WRITE_HEADERS = {"Origin": ORIGIN, "X-Project-LLM-Origin": ORIGIN}


@dataclass(frozen=True)
class Account:
    user_id: UUID
    workspace_id: UUID
    token: str = field(repr=False)


async def create_account(database: Database, settings, *, system: bool = False) -> Account:
    async with database.session() as session:
        result = await AuthService(session, settings=settings).signup(
            email=f"member-{uuid4().hex}@example.com",
            password="Safe-Test-Password-123!",
            display_name="테스트 사용자",
        )
        if system:
            result.user.platform_role = "system"
        workspace_id = await session.scalar(
            select(Workspace.id).where(Workspace.created_by == result.user.id)
        )
        account = Account(result.user.id, workspace_id, result.raw_token)
        await session.commit()
        return account


def sign_in(client: httpx.AsyncClient, account: Account) -> None:
    client.cookies.clear()
    client.cookies.set("project_llm_session", account.token)
    client.headers["X-CSRF-Token"] = csrf_token(account.token)


@pytest.fixture
async def conversation_client(schema_database: Database, postgres: IsolatedPostgres):
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
        account = await create_account(schema_database, settings)
        sign_in(client, account)
        yield client, app, account


async def create_conversation(client: httpx.AsyncClient, account: Account, **values) -> dict:
    response = await client.post(
        "/api/v1/conversations", json={"workspace_id": str(account.workspace_id), **values}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_conversation_crud_restores_messages_and_pages_without_session_leaks(
    conversation_client, schema_database
):
    client, app, account = conversation_client
    workspaces = await client.get("/api/v1/workspaces")
    assert workspaces.json() == {
        "items": [{"id": str(account.workspace_id), "name": "기본 워크스페이스", "role": "owner"}]
    }
    conversation = await create_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}"
    assert conversation["title"] == "새 대화"
    assert conversation["model"] == app.state.settings.llm_model_id
    assert conversation["active_generation_id"] is None
    async with schema_database.session() as session:
        repository = Repository(session, account.user_id)
        for index in range(4):
            await repository.append_message(
                account.workspace_id,
                UUID(conversation["id"]),
                role="user" if index % 2 == 0 else "assistant",
                content=f"저장된 메시지 {index + 1}",
                token_count=index + 2,
            )
        await session.commit()

    restored = await client.get(path)
    assert restored.status_code == 200
    assert restored.json()["id"] == conversation["id"]
    messages = await client.get(f"{path}/messages", params={"limit": 2})
    assert messages.status_code == 200
    assert [item["sequence"] for item in messages.json()["items"]] == [3, 4]
    assert messages.json()["next_cursor"] == 3
    assert messages.json()["active_generation_id"] is None
    assert set(messages.json()["items"][0]) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "status",
        "sequence",
        "token_count",
        "created_at",
    }
    older = await client.get(f"{path}/messages", params={"limit": 2, "before": 3})
    assert [item["content"] for item in older.json()["items"]] == [
        "저장된 메시지 1",
        "저장된 메시지 2",
    ]
    assert older.json()["next_cursor"] is None

    changed = await client.patch(path, json={"title": "복원한 대화", "is_pinned": True})
    assert changed.status_code == 200
    assert changed.json()["title"] == "복원한 대화"
    assert changed.json()["is_pinned"] is True
    assert (await client.patch(path, json={"status": "archived"})).status_code == 200
    listing = "/api/v1/conversations"
    query = {"workspace_id": str(account.workspace_id)}
    assert (await client.get(listing, params=query)).json()["items"] == []
    archived = await client.get(listing, params={**query, "status": "archived"})
    assert archived.json()["items"][0]["id"] == conversation["id"]
    assert (await client.patch(path, json={"status": "active"})).status_code == 200
    second = await create_conversation(client, account, title="다음 대화")
    first_page = (await client.get(listing, params={**query, "limit": 1})).json()
    second_page = (
        await client.get(listing, params={**query, "limit": 1, "cursor": first_page["next_cursor"]})
    ).json()
    assert {first_page["items"][0]["id"], second_page["items"][0]["id"]} == {
        conversation["id"],
        second["id"],
    }
    assert second_page["next_cursor"] is None
    assert (await client.request("DELETE", path, json={})).status_code == 204
    assert (await client.get(path)).status_code == 404
    assert (await client.get(f"{path}/messages")).status_code == 404
    async with schema_database.session() as session:
        saved = await session.get(Conversation, UUID(conversation["id"]))
        assert saved.deleted_at is not None
    assert app.state.database.engine.pool.checkedout() == 0
    for response in (workspaces, restored, messages, older, changed, archived):
        assert response.headers["cache-control"] == "no-store"


async def test_other_accounts_including_system_cannot_access_unshared_conversations(
    conversation_client, schema_database
):
    client, app, owner = conversation_client
    conversation = await create_conversation(client, owner, title="외부에 노출하면 안 되는 제목")
    path = f"/api/v1/conversations/{conversation['id']}"
    for system in (False, True):
        outsider = await create_account(schema_database, app.state.settings, system=system)
        sign_in(client, outsider)
        requests = [
            ("GET", path, {}),
            ("GET", f"{path}/messages", {}),
            ("PATCH", path, {"json": {"title": "탈취"}}),
            ("DELETE", path, {"json": {}}),
            ("GET", "/api/v1/conversations", {"params": {"workspace_id": str(owner.workspace_id)}}),
            ("POST", "/api/v1/conversations", {"json": {"workspace_id": str(owner.workspace_id)}}),
        ]
        for method, url, arguments in requests:
            response = await client.request(method, url, **arguments)
            assert response.status_code == 404, response.text
            assert conversation["title"] not in response.text
    sign_in(client, owner)
    assert (await client.get(path)).json()["title"] == conversation["title"]


async def test_workspace_member_reads_but_only_author_or_admin_can_edit(
    conversation_client, schema_database
):
    client, app, owner = conversation_client
    conversation = await create_conversation(client, owner)
    path = f"/api/v1/conversations/{conversation['id']}"
    member = await create_account(schema_database, app.state.settings)
    async with schema_database.session() as session:
        session.add(
            WorkspaceMember(workspace_id=owner.workspace_id, user_id=member.user_id, role="member")
        )
        await session.commit()
    sign_in(client, member)
    assert (await client.get(path)).status_code == 200
    rows = (await client.get("/api/v1/workspaces")).json()["items"]
    assert next(row for row in rows if row["id"] == str(owner.workspace_id))["role"] == "member"
    assert (await client.patch(path, json={"is_pinned": True})).status_code == 404
    assert (await client.request("DELETE", path, json={})).status_code == 404
    created = await client.post(
        "/api/v1/conversations",
        json={"workspace_id": str(owner.workspace_id), "title": "구성원 대화"},
    )
    assert created.status_code == 201
    own_path = f"/api/v1/conversations/{created.json()['id']}"
    assert (await client.patch(own_path, json={"title": "작성자 변경"})).status_code == 200
    async with schema_database.session() as session:
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == owner.workspace_id,
                WorkspaceMember.user_id == member.user_id,
            )
            .values(role="admin")
        )
        await session.commit()
    assert (await client.patch(path, json={"title": "관리자 변경"})).status_code == 200
    assert (await client.request("DELETE", path, json={})).status_code == 204


async def test_write_boundaries_reject_unknown_fields_nulls_and_invalid_cursors(
    conversation_client, caplog
):
    client, app, account = conversation_client
    conversation = await create_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}"
    private = "본문 검증 오류에 표시하면 안 되는 값"
    for payload in (
        {},
        {"title": None},
        {"status": None},
        {"is_pinned": None},
        {"is_pinned": "true"},
        {"title": "  "},
        {"created_by": private},
        {"workspace_id": str(uuid4())},
        {"title": {"private": private}},
    ):
        response = await client.patch(path, json=payload)
        assert response.status_code == 422
        assert private not in response.text + caplog.text
        assert "input" not in response.text
    for field_name in ("created_by", "model", "platform_role"):
        response = await client.post(
            "/api/v1/conversations",
            json={"workspace_id": str(account.workspace_id), field_name: private},
        )
        assert response.status_code == 422
    assert (
        await client.patch(path, content="{}", headers={"content-type": "text/plain"})
    ).status_code == 415
    assert (
        await client.patch(path, json={"title": "금지"}, headers={"X-CSRF-Token": "wrong"})
    ).status_code == 403
    assert (await client.request("DELETE", path, json={"id": private})).status_code == 422
    assert (await client.get(f"{path}/messages?before=0")).status_code == 422
    assert (await client.get(f"{path}/messages?before=9223372036854775808")).status_code == 422
    assert (await client.get(f"{path}/messages?limit=1&limit=2")).status_code == 422
    assert (
        await client.get(
            "/api/v1/conversations",
            params={"workspace_id": str(account.workspace_id), "cursor": private},
        )
    ).status_code == 422
    assert (await client.get(path)).json()["title"] == conversation["title"]
    assert app.state.database.engine.pool.checkedout() == 0


async def test_workspaces_do_not_create_missing_default_and_auth_is_required(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    async with schema_database.session() as session:
        await session.execute(
            delete(WorkspaceMember).where(WorkspaceMember.user_id == account.user_id)
        )
        count = await session.scalar(select(func.count()).select_from(Workspace))
        await session.commit()
    for _ in range(2):
        assert (await client.get("/api/v1/workspaces")).json() == {"items": []}
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(Workspace)) == count
    client.cookies.clear()
    for path in ("/api/v1/workspaces", "/api/v1/conversations", "/api/v1/usage"):
        assert (await client.get(path)).status_code == 401
    assert (
        await client.post("/api/v1/conversations", json={"workspace_id": str(account.workspace_id)})
    ).status_code == 401


async def test_usage_only_reports_current_account_with_reserved_and_used_tokens(
    conversation_client, schema_database
):
    client, app, account = conversation_client
    free = await client.get("/api/v1/usage")
    assert free.json()["remaining_tokens"] == 20_000
    assert free.json()["budget_source"] == "free_monthly"
    administrator = await create_account(schema_database, app.state.settings, system=True)
    now = datetime.now(UTC)
    async with schema_database.session() as session:
        await TokenQuotaService(session, administrator.user_id).grant_budget(
            user_id=account.user_id,
            grant_key="api-usage-budget",
            token_limit=100,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )
        await session.commit()
    async with schema_database.session() as session:
        quota = TokenQuotaService(session, account.user_id)
        await quota.reserve(request_key="settled", reserved_tokens=40)
        await quota.settle(request_key="settled", input_tokens=5, output_tokens=7)
        await quota.reserve(request_key="pending", reserved_tokens=20)
        await session.commit()
    balance = await client.get("/api/v1/usage")
    assert balance.status_code == 200
    assert balance.json()["user_id"] == str(account.user_id)
    assert balance.json()["unlimited"] is False
    assert balance.json()["token_limit"] == 100
    assert balance.json()["used_tokens"] == 12
    assert balance.json()["reserved_tokens"] == 20
    assert balance.json()["remaining_tokens"] == 68
    assert balance.headers["cache-control"] == "no-store"
    assert (
        await client.get("/api/v1/usage", params={"user_id": str(administrator.user_id)})
    ).status_code == 422
    sign_in(client, administrator)
    system_balance = (await client.get("/api/v1/usage")).json()
    assert system_balance["unlimited"] is True
    assert system_balance["remaining_tokens"] is None
    assert system_balance["user_id"] == str(administrator.user_id)


async def test_active_generation_is_disclosed_only_in_scope_and_blocks_archive_and_delete(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    conversation = await create_conversation(client, account)
    conversation_id = UUID(conversation["id"])
    path = f"/api/v1/conversations/{conversation_id}"
    now = datetime.now(UTC)
    async with schema_database.session() as session:
        budget = TokenBudget(
            user_id=account.user_id,
            grant_key="active-run-budget",
            grant_fingerprint="a" * 64,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
            token_limit=100,
        )
        session.add(budget)
        await session.flush()
        reservation = await TokenQuotaService(session, account.user_id).reserve(
            request_key="active-run", reserved_tokens=20
        )
        repository = Repository(session, account.user_id)
        user_message = await repository.append_message(
            account.workspace_id, conversation_id, role="user", content="작업 중인 질문"
        )
        assistant_message = await repository.append_message(
            account.workspace_id, conversation_id, role="assistant", content="", status="pending"
        )
        run = GenerationRun(
            workspace_id=account.workspace_id,
            conversation_id=conversation_id,
            user_id=account.user_id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            reservation_id=reservation.id,
            idempotency_key=uuid4(),
            request_hash="b" * 64,
            request_messages=[{"role": "user", "content": "작업 중인 질문"}],
            options={},
            prompt_tokens=10,
            max_output_tokens=10,
            status="running",
            started_at=now,
        )
        session.add(run)
        await session.flush()
        run_id = run.id
        await session.commit()
    for suffix in ("", "/messages"):
        response = await client.get(f"{path}{suffix}")
        assert response.json()["active_generation_id"] == str(run_id)
    listing = await client.get(
        "/api/v1/conversations", params={"workspace_id": str(account.workspace_id)}
    )
    assert listing.json()["items"][0]["active_generation_id"] == str(run_id)
    assert (await client.patch(path, json={"status": "archived"})).status_code == 409
    assert (await client.request("DELETE", path, json={})).status_code == 409
    assert (await client.patch(path, json={"is_pinned": True})).status_code == 200
    async with schema_database.session() as session:
        saved = await session.get(Conversation, conversation_id)
        assert saved.status == "active" and saved.deleted_at is None
        await session.execute(
            update(GenerationRun)
            .where(GenerationRun.id == run_id)
            .values(status="usage_pending", completed_at=datetime.now(UTC))
        )
        await session.commit()
    assert (await client.get(path)).json()["active_generation_id"] is None
    assert (await client.patch(path, json={"status": "archived"})).status_code == 200
    assert (await client.request("DELETE", path, json={})).status_code == 204


async def test_revoked_membership_and_disabled_user_stop_existing_session_reads(
    conversation_client, schema_database
):
    client, _, account = conversation_client
    conversation = await create_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}"
    async with schema_database.session() as session:
        await session.execute(
            delete(WorkspaceMember).where(WorkspaceMember.user_id == account.user_id)
        )
        await session.commit()
    assert (await client.get(path)).status_code == 404
    assert (await client.patch(path, json={"title": "소속 철회 후 변경"})).status_code == 404
    async with schema_database.session() as session:
        await session.execute(
            update(User).where(User.id == account.user_id).values(status="disabled")
        )
        await session.commit()
    for url in (path, "/api/v1/workspaces", "/api/v1/usage"):
        assert (await client.get(url)).status_code == 401
