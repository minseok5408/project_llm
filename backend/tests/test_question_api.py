"""질문 응답 API의 인증·엄격한 입력·새로고침 복원을 검증한다."""

from uuid import uuid4

import pytest

from backend.tests.test_generation_api import execute_next, login_new_account, new_conversation
from backend.tests.test_generation_api import generation_client as generation_client
from backend.tests.test_question_cards import CARD, QuestionProvider

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def create_card(generation_client):
    client, app, _ = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    provider = QuestionProvider(app.state.settings)
    app.state.generations.provider = provider
    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "필요한 정보를 물어봐줘", "options": {"max_tokens": 512}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    await execute_next(app)
    return conversation, response.json(), provider


async def test_card_response_auth_csrf_and_bounded_payload(generation_client):
    client, _, _ = generation_client
    path = f"/api/v1/generations/{uuid4()}/respond"
    assert (await client.post(path, json={"answers": ["문서"]})).status_code == 401
    _, run, _ = await create_card(generation_client)
    path = f"/api/v1/generations/{run['id']}/respond"
    headers = {"Idempotency-Key": str(uuid4())}
    csrf = client.headers.pop("X-CSRF-Token")
    assert (await client.post(path, json={"answers": ["문서"]}, headers=headers)).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    for body in (
        {"answers": []},
        {"answers": [" "]},
        {"answers": [True]},
        {"answers": ["가" * 2001]},
        {"answers": ["문서"], "user_id": "attacker"},
        {"answers": ["문서", "코드"]},
    ):
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 422, response.text
        assert "attacker" not in response.text
    assert (await client.post(path, json={"answers": ["문서"]})).status_code == 422
    await login_new_account(client)
    assert (await client.post(path, json={"answers": ["문서"]}, headers=headers)).status_code == 404


async def test_card_and_progress_reload_and_response_retry(generation_client):
    client, app, _ = generation_client
    conversation, run, provider = await create_card(generation_client)
    messages_path = f"/api/v1/conversations/{conversation['id']}/messages"
    response = await client.get(messages_path)
    message = response.json()["items"][-1]
    assert response.headers["cache-control"] == "no-store"
    assert message["question_card"] == CARD and message["can_respond"]
    assert all(item["status"] == "completed" for item in message["progress"])
    assert (await client.get(f"/api/v1/generations/{run['id']}")).json()["progress"] == message[
        "progress"
    ]
    assert (await client.get("/api/v1/generations/active")).json()["items"] == []
    provider.card = None
    path = f"/api/v1/generations/{run['id']}/respond"
    headers = {"Idempotency-Key": str(uuid4())}
    body = {
        "answers": ["문서에 사용자 선택을 반영해줘"],
        "network_mode": "local",
        "web_search": "off",
    }
    accepted = await client.post(path, json=body, headers=headers)
    assert accepted.status_code == 202 and accepted.headers["cache-control"] == "no-store"
    retry = await client.post(path, json=body, headers=headers)
    assert retry.json()["id"] == accepted.json()["id"]
    await execute_next(app)
    messages = (await client.get(messages_path)).json()["items"]
    assert len(messages) == 4
    assert not messages[1]["can_respond"]
    assert messages[1]["question_card"]["answers"] == body["answers"]
    assert messages[-1]["generation_status"] == "completed"
