"""명시적으로 선택한 기존 로컬 모델로 검색 판단·worker·정산·SSE를 검사한다.

RUN_LOCAL_SEARCH_MODEL_SMOKE=1일 때만 실행하며 격리 테스트 DB를 사용한다.
검색 공급자는 합성 자료만 반환한다. pytest -s로 출력하는 한 줄 JSON에는
합성 질문·자료·답변과 확인된 사용량만 담으며 의미 품질의 자동 통과를 뜻하지 않는다.
"""

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.llm.protocol import ProviderDelta
from backend.app.llm.providers.mlx import MlxServerProvider
from backend.app.models import GenerationStep
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.network_mode import NetworkModeService
from backend.app.tools.web_search.context import should_search
from backend.app.tools.web_search.provider import SearchResult
from backend.evaluation.schema import local_model_url
from backend.tests.test_generation_api import (
    events,
    execute_next,
    login_new_account,
    new_conversation,
)
from backend.tests.test_generation_api import generation_client as generation_client
from backend.tests.test_generations import snapshot
from backend.tests.test_web_search_generation import FakeSearchProvider, finish_task

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_LOCAL_SEARCH_MODEL_SMOKE") != "1",
        reason="RUN_LOCAL_SEARCH_MODEL_SMOKE=1일 때만 기존 loopback 모델을 사용합니다.",
    ),
]


class ObservedMlxProvider(MlxServerProvider):
    """실제 모델 응답을 보존하고 답변 시작 직전에 후정산 경계를 관찰한다."""

    def __init__(self, settings):
        super().__init__(settings)
        self.calls: list[dict] = []
        self.answer_started = asyncio.Event()
        self.answer_release = asyncio.Event()

    def _call(self, kind: str, messages, options) -> dict:
        record = {
            "kind": kind,
            "messages": [message.model_dump() for message in messages],
            "options": options.model_dump(),
            "input_tokens": None,
            "output_tokens": None,
            "finish_reason": None,
            "tool_calls": [],
        }
        self.calls.append(record)
        return record

    @staticmethod
    def _final(record: dict, delta: ProviderDelta) -> None:
        if delta.final:
            record.update(
                input_tokens=delta.input_tokens,
                output_tokens=delta.output_tokens,
                finish_reason=delta.finish_reason,
                tool_calls=[
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in delta.tool_calls
                ],
            )

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ProviderDelta]:
        record = self._call("answer", messages, options)
        self.answer_started.set()
        await self.answer_release.wait()
        async with aclosing(super().stream(messages, options)) as stream:
            async for delta in stream:
                self._final(record, delta)
                yield delta

    async def stream_tools(self, messages, options, tools) -> AsyncIterator[ProviderDelta]:
        record = self._call("planner", messages, options)
        async with aclosing(super().stream(messages, options, tools=tools)) as stream:
            async for delta in stream:
                self._final(record, delta)
                yield delta


def install_local_model(app) -> tuple[FakeSearchProvider, ObservedMlxProvider]:
    """인증정보나 사용자 DB 설정을 읽지 않고 모델 주소를 loopback으로 제한한다."""
    settings = app.state.generations.settings
    settings.llm_base_url = local_model_url(
        os.environ.get("LOCAL_SEARCH_MODEL_BASE_URL", "http://127.0.0.1:8080/v1")
    )
    settings.llm_backend = "mlx"
    settings.llm_context_window = 32768
    settings.llm_max_history_chars = 200_000
    settings.llm_request_timeout_seconds = 180
    settings.web_search_agent_enabled = True
    settings.web_search_agent_timeout_seconds = 180
    settings.web_search_planning_max_tokens = 256
    settings.web_search_max_attempts = 2
    settings.generation_questions_enabled = False
    settings.file_rag_enabled = False
    search = FakeSearchProvider()
    model = ObservedMlxProvider(settings)
    app.state.provider = app.state.generations.provider = model
    app.state.generations.search_provider = search
    app.state.network_mode = app.state.generations.network_mode = NetworkModeService(
        app.state.database, settings, search
    )
    return search, model


async def submit_case(
    client, conversation: dict, content: str, *, web_search: str = "auto"
) -> dict:
    response = await client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "content": content,
            "options": {"thinking": False, "max_tokens": 512},
            "network_mode": "auto",
            "web_search": web_search,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def finish_with_deferred_usage_check(client, app, model, before: dict) -> None:
    """판단 사용량이 확인되어도 답변 완료 전에는 예약·차감하지 않아야 한다."""
    task = asyncio.create_task(execute_next(app))
    answer_ready = asyncio.create_task(model.answer_started.wait())
    try:
        done, _ = await asyncio.wait(
            (task, answer_ready), timeout=210, return_when=asyncio.FIRST_COMPLETED
        )
        if answer_ready not in done:
            if task.done():
                await task
            pytest.fail("실제 검색 준비가 정상 답변 단계에 도달하지 못했습니다.")
        during = (await client.get("/api/v1/usage")).json()
        assert during["used_tokens"] == before["used_tokens"]
        assert during["reserved_tokens"] == 0
        model.answer_release.set()
        await asyncio.wait_for(task, timeout=210)
    finally:
        model.answer_release.set()
        answer_ready.cancel()
        await asyncio.gather(answer_ready, return_exceptions=True)
        await finish_task(task)


async def verify_and_capture(client, app, request, model, search, before, *, case_id: str):
    saved = await snapshot(app.state.database, request["id"])
    async with app.state.database.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(GenerationStep)
                    .where(GenerationStep.generation_id == UUID(request["id"]))
                    .order_by(GenerationStep.sequence)
                )
            ).all()
        )
        session.expunge_all()
    detail_response = await client.get(f"/api/v1/generations/{request['id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    replay_response = await client.get(request["events_url"])
    assert replay_response.status_code == 200
    replay = events(replay_response)
    after = (await client.get("/api/v1/usage")).json()
    print(
        json.dumps(
            {
                "kind": "local_search_worker_smoke",
                "case_id": case_id,
                "meaning_quality": "requires_human_review",
                "question": saved.user_message.content,
                "answer": saved.assistant.content,
                "status": saved.run.status,
                "search": detail["search"],
                "queries": search.search_calls,
                "fake_connectivity_checks": search.check_calls,
                "model_calls": model.calls,
                "steps": [
                    {
                        "name": row.name,
                        "status": row.status,
                        "prompt_tokens": row.prompt_tokens,
                        "input_tokens": row.input_tokens,
                        "output_tokens": row.output_tokens,
                        "usage_basis": row.usage_basis,
                    }
                    for row in rows
                ],
                "settled_input_tokens": saved.reservation.input_tokens,
                "settled_output_tokens": saved.reservation.output_tokens,
                "charged_tokens": after["used_tokens"] - before["used_tokens"],
                "sse_events": len(replay),
            },
            ensure_ascii=False,
        )
    )
    assert saved.run.status == saved.assistant.status == "completed"
    assert saved.assistant.content.strip()
    assert saved.reservation.status == "settled"
    assert saved.reservation.usage_basis == "provider"
    assert all(row.status == "completed" for row in rows)
    llm_steps = [row for row in rows if row.kind == "llm"]
    assert len(llm_steps) == len(model.calls)
    for step, call in zip(llm_steps, model.calls, strict=True):
        assert step.usage_basis == "provider"
        assert step.prompt_tokens == step.input_tokens == call["input_tokens"] > 0
        assert step.output_tokens == call["output_tokens"] > 0
    answer_call = model.calls[-1]
    assert answer_call["kind"] == "answer" and answer_call["finish_reason"] == "stop"
    assert saved.run.prompt_tokens == answer_call["input_tokens"]
    assert saved.assistant.token_count == answer_call["output_tokens"]
    assert saved.reservation.input_tokens == sum(step.input_tokens for step in llm_steps)
    assert saved.reservation.output_tokens == sum(step.output_tokens for step in llm_steps)
    assert after["used_tokens"] - before["used_tokens"] == (
        saved.reservation.input_tokens + saved.reservation.output_tokens
    )
    assert after["reserved_tokens"] == 0
    assert [frame["id"] for frame in replay] == list(range(1, saved.run.last_event_sequence + 1))
    assert (
        "".join(frame["data"]["text"] for frame in replay if frame["event"] == "delta")
        == saved.assistant.content
    )
    done = [frame for frame in replay if frame["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["input_tokens"] == saved.reservation.input_tokens
    assert done[0]["data"]["output_tokens"] == saved.reservation.output_tokens
    assert not any(frame["event"] in ("error", "cancelled") for frame in replay)
    resumed = await client.get(
        request["events_url"], headers={"Last-Event-ID": str(replay[0]["id"])}
    )
    assert events(resumed) == replay[1:]
    await app.state.generations.finish(UUID(request["id"]))
    assert (await client.get("/api/v1/usage")).json()["used_tokens"] == after["used_tokens"]
    assert (await snapshot(app.state.database, request["id"])).run.last_event_sequence == len(
        replay
    )
    return detail, rows, replay


async def test_live_model_semantic_skip_preserves_deferred_settlement_and_sse(generation_client):
    client, app, _ = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    search, model = install_local_model(app)
    question = "오늘은 기분이 좋아"
    assert should_search(question)
    before = (await client.get("/api/v1/usage")).json()
    request = await submit_case(client, conversation, question)
    assert search.check_calls == 0 and search.search_calls == []
    await finish_with_deferred_usage_check(client, app, model, before)
    detail, rows, _ = await verify_and_capture(
        client, app, request, model, search, before, case_id="semantic_not_needed"
    )
    assert search.check_calls == 0 and search.search_calls == []
    assert detail["search"] is None
    assert [row.name for row in rows] == ["search_plan", "answer"]
    calls = model.calls[0]["tool_calls"]
    assert len(calls) == 1 and calls[0]["name"] == "finish_search"
    assert json.loads(calls[0]["arguments"])["reason"] == "not_needed"


async def test_live_model_context_search_preserves_sources_usage_and_sse(generation_client):
    client, app, _ = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    # 선행 합성 대화는 기존 모의 공급자로 저장해 본 검사에서 실제 모델 호출을 늘리지 않는다.
    seed = await submit_case(client, conversation, "이 대화에서 다룰 가상 제품 이름은 TestAlpha야.")
    await execute_next(app)
    assert (await snapshot(app.state.database, seed["id"])).run.status == "completed"
    search, model = install_local_model(app)
    today = datetime.now(UTC).date().isoformat()
    search.results = (
        SearchResult(
            "합성 TestAlpha 안정판 출시 기록",
            "https://example.org/local-search-smoke/testalpha",
            f"합성 평가 자료: 가상 제품 TestAlpha의 최신 안정판은 7.4이며 출시일은 {today}다. "
            "이 자료는 해당 날짜의 가상 출시 기록이며 실제 제품 소식이 아니다.",
        ),
    )
    before = (await client.get("/api/v1/usage")).json()
    request = await submit_case(client, conversation, "그 제품 최신 버전을 검색해줘")
    await finish_with_deferred_usage_check(client, app, model, before)
    detail, rows, replay = await verify_and_capture(
        client, app, request, model, search, before, case_id="context_search"
    )
    assert search.check_calls >= 1
    assert 1 <= len(search.search_calls) <= app.state.settings.web_search_max_attempts
    assert "TestAlpha" in search.search_calls[0]
    assert rows[0].name == "search_plan" and rows[-1].name == "answer"
    assert any(row.name == "web_search" for row in rows)
    assert detail["search"]["status"] == "completed"
    assert len(detail["search"]["sources"]) == 1
    source = detail["search"]["sources"][0]
    assert source["url"] == search.results[0].url
    assert source["snippet"] == search.results[0].snippet
    assert source["retrieved_at"]
    planner_input = json.loads(model.calls[0]["messages"][-1]["content"])
    assert "TestAlpha" in " ".join(planner_input["entries"].values())
    assert any(
        frame["event"] == "meta" and frame["data"].get("search") == detail["search"]
        for frame in replay
    )


async def test_live_model_search_off_preserves_original_input_and_settlement(generation_client):
    client, app, _ = generation_client
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    search, model = install_local_model(app)
    question = "Search for the latest stable Python release. Please answer in English."
    before = (await client.get("/api/v1/usage")).json()
    request = await submit_case(client, conversation, question, web_search="off")
    await finish_with_deferred_usage_check(client, app, model, before)
    detail, rows, _ = await verify_and_capture(
        client, app, request, model, search, before, case_id="search_off_english"
    )
    saved = await snapshot(app.state.database, request["id"])
    assert saved.user_message.content == question
    # 영어 요청에 한국어 안내 문구를 그대로 복사한 실제 회귀를 막는다.
    assert not re.search(r"[가-힣]", saved.assistant.content)
    assert re.search(r"[A-Za-z]{3,}", saved.assistant.content)
    assert search.check_calls == 0 and search.search_calls == []
    assert detail["search"]["status"] == "disabled"
    assert detail["search"]["reason"] == "search_off"
    assert [row.name for row in rows] == ["answer"]
    assert len(model.calls) == 1
    assert model.calls[0]["messages"][-1]["content"].startswith(question)
    state = json.loads(
        model.calls[0]["messages"][-1]["content"]
        .split("[Server-verified search status for this turn]\n", 1)[1]
        .splitlines()[0]
    )
    assert state == {
        "reason": "search_off",
        "evidence_available": False,
        "answer_mode": "self_contained_or_notice",
    }
