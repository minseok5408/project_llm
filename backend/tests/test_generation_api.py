"""실제 DB에서 메시지 승인, 생성 이벤트 재생과 사용자별 토큰 정산을 검증한다."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.app.db import Database
from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable
from backend.app.llm.providers.mock import MockProvider
from backend.app.main import create_app
from backend.app.models import (
    GenerationRun,
    Message,
    TokenBudget,
    TokenReservation,
    User,
)
from backend.app.runtime.worker import GenerationWorker
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = pytest.mark.postgres
ORIGIN = "http://192.168.1.20:3000"
WRITE_HEADERS = {"Origin": ORIGIN, "X-Project-LLM-Origin": ORIGIN}
PASSWORD = "Generation-Test-Password-123!"


class RecordingProvider(MockProvider):
    """모델 대기 중 다른 DB 작업이 가능한지 확인하고 서버가 구성한 문맥을 기록한다."""

    database: Database

    def __init__(self, settings):
        super().__init__(settings, delay_seconds=0)
        assert settings.database_pool_size == 1
        assert settings.database_max_overflow == 0
        self.counted_contexts: list[list[dict]] = []

    async def assert_database_available(self) -> None:
        # 연결이 하나뿐이므로 호출자가 모델을 기다리며 트랜잭션을 유지하면 획득이 실패한다.
        # 취소 감시자의 짧은 조회는 끝날 때까지 기다린 뒤 같은 연결을 재사용할 수 있다.
        async with asyncio.timeout(2), self.database.session() as session:
            assert await session.scalar(select(1)) == 1

    async def count_input(self, messages: Sequence[ChatMessage], options: GenerationOptions) -> int:
        await self.assert_database_available()
        self.counted_contexts.append([message.model_dump() for message in messages])
        return await super().count_input(messages, options)

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ProviderDelta]:
        await self.assert_database_available()
        async for delta in super().stream(messages, options):
            await self.assert_database_available()
            yield delta


@pytest.fixture
async def generation_client(schema_database: Database, postgres: IsolatedPostgres):
    settings = database_settings(postgres).model_copy(update={"signup_mode": "open"})
    provider = RecordingProvider(settings)
    app = create_app(settings=settings, provider=provider)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8000",
            headers=WRITE_HEADERS,
        ) as client,
    ):
        provider.database = app.state.database
        yield client, app, provider


async def login_new_account(client: httpx.AsyncClient) -> dict:
    email = f"generation-{uuid4().hex}@example.com"
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "display_name": "생성 테스트"},
    )
    assert signup.status_code == 200, signup.text
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    workspaces = await client.get("/api/v1/workspaces")
    return {
        "id": UUID(login.json()["user"]["id"]),
        "email": email,
        "workspace_id": workspaces.json()["items"][0]["id"],
    }


async def grant_budget(database: Database, user_id: UUID, *, limit: int = 50_000) -> None:
    now = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            TokenBudget(
                user_id=user_id,
                grant_key=f"generation-tests:{uuid4()}",
                grant_fingerprint="c" * 64,
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
                token_limit=limit,
            )
        )
        await session.commit()


async def new_conversation(client: httpx.AsyncClient, account: dict) -> dict:
    response = await client.post(
        "/api/v1/conversations", json={"workspace_id": account["workspace_id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def submit(client: httpx.AsyncClient, conversation: dict, *, key=None, content="한글 질문"):
    return await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"Idempotency-Key": str(key or uuid4())},
        json={"content": content, "options": {"max_tokens": 64, "thinking": False}},
    )


async def execute_next(app) -> UUID:
    worker = GenerationWorker(app.state.generations)
    job = await worker.claim()
    assert job is not None
    await worker.execute(job)
    return job["id"]


def events(response: httpx.Response) -> list[dict]:
    result = []
    for frame in response.text.split("\n\n"):
        fields = {}
        for line in frame.splitlines():
            if line and not line.startswith(":"):
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        if fields:
            result.append(
                {
                    "id": int(fields["id"]),
                    "event": fields["event"],
                    "data": json.loads(fields["data"]),
                }
            )
    return result


async def test_login_create_generate_replay_restore_on_second_device_and_settle_usage(
    generation_client, schema_database
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}"
    key = uuid4()
    original_balance = (await client.get("/api/v1/usage")).json()
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    assert run["status"] == "queued"
    assert run["last_event_id"] == 1
    assert (await client.get(path)).json()["active_generation_id"] == run["id"]
    pending_balance = (await client.get("/api/v1/usage")).json()
    assert pending_balance == original_balance
    assert pending_balance["reserved_tokens"] == pending_balance["used_tokens"] == 0
    assert pending_balance["remaining_tokens"] == 50_000
    repeated = await submit(client, conversation, key=key)
    assert repeated.status_code == 202 and repeated.json() == run
    assert (await client.get("/api/v1/usage")).json() == pending_balance
    assert (await submit(client, conversation, key=key, content="바뀐 질문")).status_code == 409
    assert str(await execute_next(app)) == run["id"]

    finished = await client.get(f"/api/v1/generations/{run['id']}")
    assert finished.status_code == 200
    assert finished.json()["status"] == "completed"
    replay = await client.get(run["events_url"], params={"after": 0})
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/event-stream")
    assert replay.headers["cache-control"] == "no-store, no-transform"
    frames = events(replay)
    assert frames[0]["event"] == "meta"
    assert frames[-1]["event"] == "done"
    assert [frame["id"] for frame in frames] == list(range(1, len(frames) + 1))
    assert {frame["data"]["generation_id"] for frame in frames} == {run["id"]}
    text = "".join(frame["data"]["text"] for frame in frames if frame["event"] == "delta")
    assert "한글 질문" in text
    partial = await client.get(run["events_url"], params={"after": 2})
    assert events(partial) == [frame for frame in frames if frame["id"] > 2]
    header_resume = await client.get(run["events_url"], headers={"Last-Event-ID": "2"})
    assert events(header_resume) == events(partial)
    complete_resume = await client.get(run["events_url"], params={"after": frames[-1]["id"]})
    assert events(complete_resume) == []
    assert (
        await client.get(run["events_url"], params={"after": frames[-1]["id"] + 1})
    ).status_code == 422

    messages = (await client.get(f"{path}/messages")).json()
    assert messages["active_generation_id"] is None
    assert [message["role"] for message in messages["items"]] == ["user", "assistant"]
    assert messages["items"][1]["content"] == text
    assert messages["items"][1]["status"] == "completed"
    actual_input, actual_output = finished.json()["input_tokens"], finished.json()["output_tokens"]
    assert actual_input > 0 and 0 < actual_output <= 64
    assert messages["items"][1]["token_count"] == actual_output
    balance = (await client.get("/api/v1/usage")).json()
    assert balance["reserved_tokens"] == 0
    assert balance["used_tokens"] == actual_input + actual_output
    assert balance["remaining_tokens"] == 50_000 - balance["used_tokens"]
    async with schema_database.session() as session:
        stored = await session.get(GenerationRun, UUID(run["id"]))
        reservation = await session.get(TokenReservation, stored.reservation_id)
        assert stored.request_messages == []
        assert reservation.status == "settled"
        assert reservation.charge_mode == "deferred"
        assert reservation.usage_basis == "provider"
        assert reservation.input_tokens + reservation.output_tokens == balance["used_tokens"]

    # 별도 브라우저의 새 로그인 세션도 서버에 저장된 같은 대화를 복원한다.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8000", headers=WRITE_HEADERS
    ) as second:
        login = await second.post(
            "/api/v1/auth/login", json={"email": account["email"], "password": PASSWORD}
        )
        assert login.status_code == 200
        assert (await second.get(f"{path}/messages")).json() == messages
        assert (await second.get("/api/v1/usage")).json() == balance
        assert events(await second.get(run["events_url"])) == frames
    replayed_request = await submit(client, conversation, key=key)
    assert replayed_request.status_code == 202
    assert replayed_request.json()["status"] == "completed"
    assert await GenerationWorker(app.state.generations).claim() is None
    assert (await client.get("/api/v1/usage")).json() == balance
    assert provider.counted_contexts[0][0]["role"] == "system"
    assert provider.counted_contexts[0][-1] == {"role": "user", "content": "한글 질문"}
    next_turn = await submit(client, conversation, content="앞선 답변을 이어서 설명해줘")
    assert next_turn.status_code == 202
    assert [message["role"] for message in provider.counted_contexts[-1]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert provider.counted_contexts[-1][2]["content"] == text
    await execute_next(app)
    assert app.state.database.engine.pool.checkedout() == 0


async def test_generation_routes_require_login_and_private_runs_do_not_cross_accounts(
    generation_client, schema_database
):
    client, app, _ = generation_client
    random_id = uuid4()
    for path in (
        f"/api/v1/generations/{random_id}",
        f"/api/v1/generations/{random_id}/events",
    ):
        assert (await client.get(path)).status_code == 401
    assert (
        await client.post(f"/api/v1/generations/{random_id}/cancel", json={})
    ).status_code == 401
    assert (
        await client.post(f"/api/v1/conversations/{random_id}/messages", json={"content": "질문"})
    ).status_code == 401
    owner = await login_new_account(client)
    await grant_budget(schema_database, owner["id"])
    conversation = await new_conversation(client, owner)
    accepted = await submit(client, conversation)
    assert accepted.status_code == 202
    run = accepted.json()
    await execute_next(app)
    for system in (False, True):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1:8000",
            headers=WRITE_HEADERS,
        ) as outsider:
            account = await login_new_account(outsider)
            if system:
                async with schema_database.session() as session:
                    await session.execute(
                        update(User).where(User.id == account["id"]).values(platform_role="system")
                    )
                    await session.commit()
            for path in (f"/api/v1/generations/{run['id']}", run["events_url"]):
                response = await outsider.get(path)
                assert response.status_code == 404
                assert "한글 질문" not in response.text
            assert (
                await outsider.post(f"/api/v1/generations/{run['id']}/cancel", json={})
            ).status_code == 404
            assert (await submit(outsider, conversation)).status_code == 404
    assert (await client.get(f"/api/v1/generations/{run['id']}")).json()["status"] == "completed"


async def test_generation_writes_reject_csrf_history_roles_and_unknown_options(
    generation_client, schema_database, caplog
):
    client, _, _ = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}/messages"
    headers = {"Idempotency-Key": str(uuid4())}
    private = "임의 시스템 지시와 숨겨야 할 입력"
    for payload in (
        {"content": "질문", "role": "system"},
        {"content": "질문", "messages": [{"role": "system", "content": private}]},
        {"content": "질문", "user_id": str(uuid4())},
        {"content": "질문", "options": {"system_prompt": private}},
        {"content": "질문", "options": {"max_tokens": "64"}},
        {"content": "질문", "options": {"thinking": "true"}},
        {"content": "질문", "options": {"max_tokens": 4097}},
        {"content": {"private": private}},
    ):
        response = await client.post(path, headers=headers, json=payload)
        assert response.status_code == 422
        assert private not in response.text + caplog.text
    assert (
        await client.post(
            path, headers={**headers, "X-CSRF-Token": "wrong"}, json={"content": "질문"}
        )
    ).status_code == 403
    assert (await client.post(path, headers=headers, content="content=질문")).status_code == 415
    assert (await client.post(path, json={"content": "질문"})).status_code == 422
    assert (
        await client.post(path, headers={"Idempotency-Key": "invalid"}, json={"content": "질문"})
    ).status_code == 422
    assert (
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "질문"}]})
    ).status_code == 410
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(GenerationRun)) == 0
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 0


async def test_budget_is_checked_before_saving_messages_and_system_still_has_context_limit(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"], limit=0)
    conversation = await new_conversation(client, account)
    response = await submit(client, conversation)
    assert response.status_code == 402
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()[
        "items"
    ] == []
    assert (await client.get("/api/v1/usage")).json()["reserved_tokens"] == 0
    async with schema_database.session() as session:
        await session.execute(
            update(User).where(User.id == account["id"]).values(platform_role="system")
        )
        await session.commit()
    app.state.settings.llm_context_window = 1024

    async def measured_count(messages, options):
        await provider.assert_database_available()
        assert len(messages[-1].content) < 10
        return app.state.settings.llm_context_window

    monkeypatch.setattr(provider, "count_input", measured_count)
    response = await submit(client, conversation)
    assert response.status_code == 422
    assert "문맥 한도" in response.json()["detail"]
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(GenerationRun)) == 0
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 0
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
    assert (await client.get("/api/v1/usage")).json()["unlimited"] is True


async def test_signup_can_generate_with_monthly_free_tokens_without_manual_grant(
    generation_client, schema_database
):
    client, app, _ = generation_client
    account = await login_new_account(client)
    async with schema_database.session() as session:
        budgets = list(
            (
                await session.scalars(
                    select(TokenBudget).where(TokenBudget.user_id == account["id"])
                )
            ).all()
        )
        assert len(budgets) == 1
        assert budgets[0].source == "free_monthly"
        assert budgets[0].token_limit == 20_000
    conversation = await new_conversation(client, account)
    accepted = await submit(client, conversation)
    assert accepted.status_code == 202
    await execute_next(app)
    balance = (await client.get("/api/v1/usage")).json()
    assert balance["budget_source"] == "free_monthly"
    assert balance["plan_name"] == "무료"
    assert balance["used_tokens"] > 0
    assert balance["reserved_tokens"] == 0
    assert balance["remaining_tokens"] == 20_000 - balance["used_tokens"]
    assert (await client.get("/api/v1/usage")).json() == balance


async def test_output_limit_fits_remaining_balance_without_charging_before_completion(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"], limit=13)
    conversation = await new_conversation(client, account)

    async def measured_count(messages, options):
        await provider.assert_database_available()
        return 10

    monkeypatch.setattr(provider, "count_input", measured_count)
    key = uuid4()
    original_balance = (await client.get("/api/v1/usage")).json()
    # 64토큰 출력을 요청해도 잔액으로 가능한 3토큰까지 응답할 수 있어야 한다.
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    assert (await client.get("/api/v1/usage")).json() == original_balance
    repeated = await submit(client, conversation, key=key)
    assert repeated.status_code == 202 and repeated.json() == run
    async with schema_database.session() as session:
        stored = await session.get(GenerationRun, UUID(run["id"]))
        assert stored.prompt_tokens == 10
        assert stored.max_output_tokens == 3
        assert stored.thinking is False
        reservation = await session.get(TokenReservation, stored.reservation_id)
        assert reservation.charge_mode == "deferred"
        assert reservation.status == "reserved"
        assert reservation.input_tokens is reservation.output_tokens is None
        budget = await session.get(TokenBudget, reservation.budget_id)
        assert budget.used_tokens == budget.reserved_tokens == 0
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 1

    await execute_next(app)
    finished = (await client.get(f"/api/v1/generations/{run['id']}")).json()
    assert finished["status"] == "completed"
    assert finished["input_tokens"] == 10
    assert finished["output_tokens"] == 3
    balance = (await client.get("/api/v1/usage")).json()
    assert balance["used_tokens"] == 13
    assert balance["reserved_tokens"] == balance["remaining_tokens"] == 0
    assert (await submit(client, conversation, key=key)).json()["status"] == "completed"
    assert (await client.get("/api/v1/usage")).json() == balance
    assert (await submit(client, await new_conversation(client, account))).status_code == 402


@pytest.mark.parametrize("budget_limit", [9, 10])
async def test_no_request_is_saved_without_budget_for_input_and_one_output_token(
    generation_client, schema_database, monkeypatch, budget_limit
):
    client, _, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"], limit=budget_limit)
    conversation = await new_conversation(client, account)

    async def measured_count(messages, options):
        await provider.assert_database_available()
        return 10

    monkeypatch.setattr(provider, "count_input", measured_count)
    original_balance = (await client.get("/api/v1/usage")).json()
    rejected = await submit(client, conversation)
    assert rejected.status_code == 402, rejected.text
    assert (await client.get("/api/v1/usage")).json() == original_balance
    async with schema_database.session() as session:
        for model in (GenerationRun, Message, TokenReservation):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_unknown_final_usage_waives_charge_and_allows_another_answer(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    original_balance = (await client.get("/api/v1/usage")).json()

    async def interrupted_stream(messages, options):
        yield ProviderDelta(text="일부 응답", received_output_tokens=1)
        raise ProviderUnavailable("테스트용 연결 오류", request_started=True)

    original_stream = provider.stream
    monkeypatch.setattr(provider, "stream", interrupted_stream)
    key = uuid4()
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    await execute_next(app)
    failed = (await client.get(f"/api/v1/generations/{run['id']}")).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "provider_unavailable"
    assert failed["input_tokens"] == failed["output_tokens"] == 0
    assert failed["usage_basis"] == "waived"
    assert (await client.get("/api/v1/usage")).json() == original_balance
    # 사용량을 모르는 실패는 청구를 면제하고, 실제 출력이 0이었다고 기록하지 않는다.
    restored = (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()
    assert restored["active_generation_id"] is None
    assert restored["items"][1]["status"] == "failed"
    assert restored["items"][1]["content"] == "일부 응답"
    assert restored["items"][1]["token_count"] is None
    frames = events(await client.get(run["events_url"]))
    assert frames[-1]["event"] == "error"
    repeated = await submit(client, conversation, key=key)
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "failed"
    assert (await client.get("/api/v1/usage")).json() == original_balance
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(GenerationRun)) == 1
        assert await session.scalar(select(func.count()).select_from(Message)) == 2
        reservation = await session.scalar(select(TokenReservation))
        assert reservation.status == "released"
        assert reservation.charge_mode == "deferred"
        assert reservation.usage_basis == "waived"
        assert reservation.input_tokens == reservation.output_tokens == 0

    # 새로고침으로 읽는 대화와 같은 대화의 다음 생성이 별도 수동 정산 없이 동작한다.
    monkeypatch.setattr(provider, "stream", original_stream)
    next_answer = await submit(client, conversation, content="다음 답변을 생성해줘")
    assert next_answer.status_code == 202, next_answer.text
    await execute_next(app)
    completed = (await client.get(f"/api/v1/generations/{next_answer.json()['id']}")).json()
    assert completed["status"] == "completed"
    assert completed["usage_basis"] == "provider"
    balance = (await client.get("/api/v1/usage")).json()
    assert balance["used_tokens"] == completed["input_tokens"] + completed["output_tokens"]
    assert balance["reserved_tokens"] == 0
    assert [message["role"] for message in provider.counted_contexts[-1]] == [
        "system",
        "user",
        "user",
    ]
    context = provider.counted_contexts[-1]
    assert context[-2]["content"] == "한글 질문"
    # 짧은 실패 원문도 삭제하지 않고 완성 답변과 구분한 참고 기록으로 전달한다.
    assert '"assistant_partial":"일부 응답"' in context[0]["content"]


async def test_failure_after_final_usage_still_charges_confirmed_tokens_once(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)

    async def fails_after_final_usage(messages, options):
        yield ProviderDelta(text="확인된 일부 응답", received_output_tokens=3)
        yield ProviderDelta(
            input_tokens=await provider.count_input(messages, options),
            output_tokens=3,
            final=True,
            received_output_tokens=3,
        )
        raise ProviderUnavailable("사용량 전달 후 연결 오류", request_started=True)

    monkeypatch.setattr(provider, "stream", fails_after_final_usage)
    key = uuid4()
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    await execute_next(app)
    failed = (await client.get(f"/api/v1/generations/{run['id']}")).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "provider_unavailable"
    assert failed["usage_basis"] == "provider"
    assert failed["input_tokens"] > 0
    assert failed["output_tokens"] == 3
    original_balance = (await client.get("/api/v1/usage")).json()
    assert original_balance["used_tokens"] == failed["input_tokens"] + 3
    assert original_balance["reserved_tokens"] == 0
    restored = (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()
    assert restored["active_generation_id"] is None
    assert restored["items"][1]["status"] == "failed"
    assert restored["items"][1]["token_count"] == 3
    repeated = await submit(client, conversation, key=key)
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "failed"
    assert (await client.get("/api/v1/usage")).json() == original_balance
    async with schema_database.session() as session:
        stored = await session.get(GenerationRun, UUID(run["id"]))
        reservation = await session.get(TokenReservation, stored.reservation_id)
        assert reservation.status == "settled"
        assert reservation.usage_basis == "provider"

    next_answer = await submit(client, conversation, content="실패 이후 새 질문")
    assert next_answer.status_code == 202, next_answer.text
    assert (await client.get("/api/v1/usage")).json() == original_balance


async def test_cancel_requires_json_and_csrf_and_queued_cancel_does_not_charge(
    generation_client, schema_database
):
    client, app, _ = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    key = uuid4()
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202
    run = accepted.json()
    path = f"/api/v1/generations/{run['id']}/cancel"
    assert (await client.post(path, json={}, headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (
        await client.post(path, content="{}", headers={"content-type": "text/plain"})
    ).status_code == 415
    assert (await client.post(path, json={"user_id": str(uuid4())})).status_code == 422
    cancelled = await client.post(path, json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested"] is True
    assert await GenerationWorker(app.state.generations).claim() is None
    assert (await client.post(path, json={})).json() == cancelled.json()
    repeated = await submit(client, conversation, key=key)
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "cancelled"
    balance = (await client.get("/api/v1/usage")).json()
    assert balance["used_tokens"] == balance["reserved_tokens"] == 0
    assert balance["remaining_tokens"] == 50_000
    async with schema_database.session() as session:
        stored = await session.get(GenerationRun, UUID(run["id"]))
        reservation = await session.get(TokenReservation, stored.reservation_id)
        assert reservation.status == "released"
        assert reservation.charge_mode == "deferred"
        assert reservation.usage_basis == "waived"
        assert reservation.input_tokens == reservation.output_tokens == 0
    frames = events(await client.get(run["events_url"]))
    assert frames[-1]["event"] == "cancelled"
    assert frames[-1]["data"]["input_tokens"] == frames[-1]["data"]["output_tokens"] == 0


async def test_running_cancel_closes_blocked_provider_and_settles_received_tokens(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    waiting = asyncio.Event()
    closed = asyncio.Event()
    never = asyncio.Event()

    async def blocked_stream(messages, options):
        try:
            # 화면에 표시할 본문이 없어도 확인된 숨김 토큰은 수신량에 포함한다.
            yield ProviderDelta(text="", received_output_tokens=1)
            waiting.set()
            await never.wait()
        finally:
            closed.set()

    monkeypatch.setattr(provider, "stream", blocked_stream)
    original_balance = (await client.get("/api/v1/usage")).json()
    key = uuid4()
    accepted = await submit(client, conversation, key=key)
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()
    path = f"/api/v1/generations/{run['id']}"
    worker = GenerationWorker(app.state.generations)
    job = await worker.claim()
    assert job is not None and str(job["id"]) == run["id"]
    execution = asyncio.create_task(worker.execute(job))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert (await client.get(path)).json()["status"] == "running"
        # 수신이 시작되어도 답변을 완료하거나 중단하기 전에는 잔액을 줄이지 않는다.
        assert (await client.get("/api/v1/usage")).json() == original_balance
        assert not execution.done()
        assert (
            await client.post(f"{path}/cancel", json={}, headers={"X-CSRF-Token": "wrong"})
        ).status_code == 403
        assert not closed.is_set()

        cancelled = await client.post(f"{path}/cancel", json={})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancel_requested"] is True
        # 최종 usage나 다음 토큰이 오지 않아도 중단이 상류 생성기를 직접 닫아야 한다.
        await asyncio.wait_for(closed.wait(), timeout=2)
        await asyncio.wait_for(execution, timeout=5)
        assert not never.is_set()

        finished = await client.get(path)
        assert finished.status_code == 200
        result = finished.json()
        assert result["status"] == "cancelled"
        assert result["usage_basis"] == "received"
        assert result["input_tokens"] == job["prompt_tokens"] > 0
        assert result["output_tokens"] == 1
        replay = await client.get(run["events_url"])
        assert replay.status_code == 200
        frames = events(replay)
        assert frames[-1]["event"] == "cancelled"
        assert frames[-1]["data"]["input_tokens"] == result["input_tokens"]
        assert frames[-1]["data"]["output_tokens"] == 1
        assert not any(frame["event"] == "delta" for frame in frames)

        balance = (await client.get("/api/v1/usage")).json()
        assert balance["reserved_tokens"] == 0
        assert balance["used_tokens"] == job["prompt_tokens"] + 1
        assert balance["remaining_tokens"] == 50_000 - balance["used_tokens"]
        async with schema_database.session() as session:
            stored = await session.get(GenerationRun, UUID(run["id"]))
            reservation = await session.get(TokenReservation, stored.reservation_id)
            assert reservation.status == "settled"
            assert reservation.charge_mode == "deferred"
            assert reservation.usage_basis == "received"
            assert reservation.input_tokens == result["input_tokens"]
            assert reservation.output_tokens == 1
        # 중단 요청과 원래 요청을 반복해도 이미 확정한 사용량은 다시 차감하지 않는다.
        assert (await client.post(f"{path}/cancel", json={})).json()["status"] == "cancelled"
        assert (await submit(client, conversation, key=key)).json()["status"] == "cancelled"
        assert (await client.get("/api/v1/usage")).json() == balance
        assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()[
            "active_generation_id"
        ] is None
        assert app.state.database.engine.pool.checkedout() == 0
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


async def test_second_submit_cannot_miss_a_turn_that_finishes_during_token_count(
    generation_client, schema_database, monkeypatch
):
    client, app, provider = generation_client
    account = await login_new_account(client)
    await grant_budget(schema_database, account["id"])
    conversation = await new_conversation(client, account)
    first = await submit(client, conversation, content="첫 질문")
    assert first.status_code == 202
    first_run = first.json()
    original_count = provider.count_input
    entered = False

    async def finish_before_counting(messages, options):
        nonlocal entered
        if not entered:
            entered = True
            # 승인 전 토큰 계산과 직전 응답 완료가 겹치는 순서를 재현한다.
            monkeypatch.setattr(provider, "count_input", original_count)
            await execute_next(app)
        return await original_count(messages, options)

    monkeypatch.setattr(provider, "count_input", finish_before_counting)
    second = await submit(client, conversation, content="둘째 질문")
    if second.status_code == 202:
        # 완료 상태 버전을 재검사하는 구현은 최신 문맥으로 다시 계산해도 된다.
        async with schema_database.session() as session:
            run = await session.get(GenerationRun, UUID(second.json()["id"]))
            assert [item["role"] for item in run.request_messages] == [
                "system",
                "user",
                "assistant",
                "user",
            ]
            assert run.request_messages[1]["content"] == "첫 질문"
        await execute_next(app)
    else:
        assert second.status_code in (409, 429)
    async with schema_database.session() as session:
        initial = await session.get(GenerationRun, UUID(first_run["id"]))
        assert initial is not None
    assert app.state.database.engine.pool.checkedout() == 0
