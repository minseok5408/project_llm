"""검색 근거의 API 복원·재생·권한 및 재생성 설정을 실제 DB에서 검증한다."""

from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from backend.app.services.network_mode import NetworkModeService
from backend.app.tools.web_search.provider import build_search_provider
from backend.tests.test_generation_api import (
    events,
    execute_next,
    login_new_account,
    new_conversation,
)
from backend.tests.test_generation_api import generation_client as generation_client

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("search_provider", ["tavily", "brave"])
async def test_search_sources_restore_replay_and_preserve_previous_answer(
    generation_client, search_provider
):
    client, app, _model = generation_client
    searches = []

    def respond(request):
        if request.method == "HEAD":
            return httpx.Response(200)
        searches.append(request)
        result = {
            "title": "공식 검증 자료",
            "url": "https://example.com/current",
            "content"
            if search_provider == "tavily"
            else "description": "검색 공급자가 반환한 참고 요약입니다.",
        }
        payload = {"results": [result]}
        return httpx.Response(
            200, json=payload if search_provider == "tavily" else {"web": payload}
        )

    service = app.state.generations
    settings = service.settings.model_copy(
        update={
            "web_search_api_key": SecretStr("test-key"),
            **({"web_search_provider": "brave"} if search_provider == "brave" else {}),
        }
    )
    service.search_provider = build_search_provider(
        settings, transport=httpx.MockTransport(respond)
    )
    service.network_mode = NetworkModeService(service.database, settings, service.search_provider)
    app.state.network_mode = service.network_mode
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    path = f"/api/v1/conversations/{conversation['id']}/messages"
    key = str(uuid4())
    body = {
        "content": "오늘 뉴스를 검색해줘",
        "options": {"max_tokens": 64},
        "network_mode": "auto",
        "web_search": "on",
    }
    response = await client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert response.status_code == 202, response.text
    generation = response.json()
    repeated = await client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert repeated.json() == generation
    changed = await client.post(
        path, headers={"Idempotency-Key": key}, json={**body, "web_search": "off"}
    )
    assert changed.status_code == 409
    await execute_next(app)
    assert len(searches) == 1
    detail = (await client.get(f"/api/v1/generations/{generation['id']}")).json()
    assert detail["status"] == "completed"
    assert detail["search"]["provider"] == search_provider
    source = detail["search"]["sources"][0]
    assert source["number"] == 1
    assert source["url"] == "https://example.com/current"
    assert source["retrieved_at"]
    restored = (await client.get(path)).json()["items"][-1]
    assert restored["search"] == detail["search"]
    replay = events(await client.get(generation["events_url"]))
    assert any(frame["data"].get("search") == detail["search"] for frame in replay)

    regenerated = await client.post(
        f"/api/v1/generations/{generation['id']}/regenerate",
        headers={"Idempotency-Key": str(uuid4())},
        json={"options": {"max_tokens": 64}, "network_mode": "local", "web_search": "off"},
    )
    assert regenerated.status_code == 202, regenerated.text
    await execute_next(app)
    assert len(searches) == 1
    answers = [
        row for row in (await client.get(path)).json()["items"] if row["role"] == "assistant"
    ]
    assert len(answers) == 2
    assert answers[0]["search"] == detail["search"]
    assert not answers[0]["is_current"] and answers[1]["is_current"]
    assert answers[1]["search"]["sources"] == []
    assert answers[1]["search"]["status"] == "disabled"

    # 다른 계정으로 전환해도 저장된 출처를 포함한 생성·메시지 접근은 허용하지 않는다.
    await login_new_account(client)
    assert (await client.get(path)).status_code == 404
    assert (await client.get(f"/api/v1/generations/{generation['id']}")).status_code == 404


@pytest.mark.parametrize("field,value", [("network_mode", "wifi"), ("web_search", True)])
async def test_generation_rejects_invalid_search_options(generation_client, field, value):
    client, _app, _provider = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"Idempotency-Key": str(uuid4())},
        json={"content": "테스트 질문", field: value},
    )
    assert response.status_code == 422
