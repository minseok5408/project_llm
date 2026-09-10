"""문맥 표시의 실제 측정값·요약 범위·실패 상태와 접근 권한을 검증한다."""

import asyncio
from uuid import UUID

import httpx
import pytest
from sqlalchemy import delete

from backend.app.context.status import context_status
from backend.app.models import GenerationEvent, GenerationRun, WorkspaceMember
from backend.tests.test_compaction_generation import enable_compaction, install_provider, seed
from backend.tests.test_generation_api import (
    WRITE_HEADERS,
    execute_next,
    login_new_account,
    new_conversation,
    submit,
)
from backend.tests.test_generation_api import generation_client as generation_client
from backend.tests.test_generations import Harness, snapshot
from backend.tests.test_generations import harness as harness

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def status(harness: Harness, run_id: str):
    async with harness.database.session() as session:
        run = await session.get(GenerationRun, UUID(run_id))
        return await context_status(session, run)


async def test_context_uses_recorded_window_and_reports_confirmed_answer_output(harness: Harness):
    request = await harness.submit()
    admitted = await status(harness, request["id"])
    assert admitted["input_tokens"] > 0
    assert admitted["output_tokens"] is None
    assert admitted["summary_through_sequence"] == 0
    assert admitted["phase"] == "preparing"
    await harness.execute_next()
    finished = await status(harness, request["id"])
    result = await snapshot(harness.database, request["id"])
    assert finished["input_tokens"] == result.reservation.input_tokens
    assert finished["output_tokens"] == result.reservation.output_tokens
    assert finished["phase"] == "finished"
    harness.service.settings = harness.settings.model_copy(update={"llm_context_window": 1024})
    assert (await status(harness, request["id"]))["context_window"] == admitted["context_window"]
    assert any(event.payload.get("context", {}).get("phase") == "ready" for event in result.events)


async def test_compaction_hides_temporary_admission_count_and_freezes_used_summary_range(
    harness: Harness,
):
    install_provider(harness)
    await seed(harness, harness.system)
    enable_compaction(harness)
    request = await harness.submit(content="내 정보를 다시 알려줘")
    pending = await status(harness, request["id"])
    assert pending["input_tokens"] is None
    assert pending["summary_through_sequence"] is None
    assert pending["compaction_status"] == "pending"
    await harness.execute_next()
    first = await status(harness, request["id"])
    assert first["compaction_status"] == "completed"
    assert first["summary_through_sequence"] == 4
    assert first["input_tokens"] == 460
    assert first["output_tokens"] == 5
    for index in range(3):
        following = await harness.submit(content=f"후속 질문 {index}")
        await harness.execute_next()
    later = await status(harness, following["id"])
    assert later["summary_through_sequence"] > first["summary_through_sequence"]
    assert await status(harness, request["id"]) == first


@pytest.mark.parametrize("failure", ["blank", "length", "wrong_input_usage"])
async def test_failed_summary_does_not_report_fake_zero_or_success(harness: Harness, failure: str):
    provider = install_provider(harness)
    await seed(harness, harness.system)
    enable_compaction(harness)
    provider.summary_mode = failure
    request = await harness.submit()
    await harness.execute_next()
    failed = await status(harness, request["id"])
    assert failed["phase"] == "finished"
    assert failed["compaction_status"] == "failed"
    assert failed["input_tokens"] is None
    assert failed["output_tokens"] is None
    assert failed["summary_through_sequence"] is None


async def test_running_and_cancelled_compaction_are_distinct(harness: Harness):
    provider = install_provider(harness)
    await seed(harness, harness.system)
    enable_compaction(harness)
    provider.summary_mode = "blocked"
    request = await harness.submit()
    job = await harness.worker.claim()
    task = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(provider.summary_started.wait(), timeout=5)
        assert (await status(harness, request["id"]))["compaction_status"] == "running"
        await harness.service.cancel(harness.system.user_id, UUID(request["id"]))
        await asyncio.wait_for(task, timeout=5)
        stopped = await status(harness, request["id"])
        assert stopped["compaction_status"] == "cancelled"
        assert stopped["input_tokens"] is stopped["output_tokens"] is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_legacy_generation_without_measurements_remains_unknown(harness: Harness):
    request = await harness.submit()
    await harness.execute_next()
    async with harness.database.session() as session:
        await session.execute(
            delete(GenerationEvent).where(
                GenerationEvent.generation_id == UUID(request["id"]),
                GenerationEvent.kind == "meta",
            )
        )
        await session.commit()
    assert await status(harness, request["id"]) is None


async def test_active_tasks_and_context_api_are_private_and_restore_terminal_context(
    generation_client,
    schema_database,
):
    client, app, _ = generation_client
    path = "/api/v1/generations/active"
    assert (await client.get(path)).status_code == 401
    owner = await login_new_account(client)
    conversation = await new_conversation(client, owner)
    request = (await submit(client, conversation)).json()
    active = await client.get(path)
    assert active.status_code == 200
    assert active.headers["cache-control"] == "no-store"
    assert [item["id"] for item in active.json()["items"]] == [request["id"]]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://127.0.0.1:8000",
        headers=WRITE_HEADERS,
    ) as outsider:
        await login_new_account(outsider)
        assert (await outsider.get(path)).json()["items"] == []
        assert (await outsider.get(f"/api/v1/generations/{request['id']}")).status_code == 404
        assert (
            await outsider.get(f"/api/v1/conversations/{conversation['id']}/messages")
        ).status_code == 404
    await execute_next(app)
    assert (await client.get(path)).json()["items"] == []
    run = (await client.get(f"/api/v1/generations/{request['id']}")).json()
    messages = (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()
    assert messages["context"] == run["context"]
    assert run["context"]["phase"] == "finished"
    assert run["context"]["output_tokens"] == run["output_tokens"]
    await submit(client, conversation)
    async with schema_database.session() as session:
        await session.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == owner["id"]))
        await session.commit()
    assert (await client.get(path)).json()["items"] == []
