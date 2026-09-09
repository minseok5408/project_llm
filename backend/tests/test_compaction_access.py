"""요약 중 접근 권한을 잃으면 요약 재사용과 답변 생성을 차단하는지 검증한다."""

import asyncio

import pytest
from sqlalchemy import delete, select, update

from backend.app.models import TokenBudget, User, WorkspaceMember
from backend.tests.test_compaction_generation import (
    attempts,
    checkpoint,
    enable_compaction,
    install_provider,
    seed,
)
from backend.tests.test_generations import Harness, snapshot
from backend.tests.test_generations import harness as harness

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("change", ["disabled_user", "removed_membership"])
async def test_access_revoked_during_summary_discards_checkpoint_and_waives_answer(
    harness: Harness, change: str
) -> None:
    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    provider.summary_mode = "blocked"
    answer_count = len(provider.answer_calls)
    request = await harness.submit(harness.member, content="요약 중 권한 철회 검증")
    admitted = await snapshot(harness.database, request["id"])
    async with harness.database.session() as session:
        before_used = await session.scalar(
            select(TokenBudget.used_tokens).where(TokenBudget.id == admitted.reservation.budget_id)
        )

    job = await harness.worker.claim()
    assert job is not None
    running = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(provider.summary_started.wait(), timeout=3)
        async with harness.database.session() as session:
            if change == "disabled_user":
                await session.execute(
                    update(User).where(User.id == harness.member.user_id).values(status="disabled")
                )
            else:
                await session.execute(
                    delete(WorkspaceMember).where(
                        WorkspaceMember.user_id == harness.member.user_id,
                        WorkspaceMember.workspace_id == harness.member.workspace_id,
                    )
                )
            await session.commit()
        provider.summary_release.set()
        await asyncio.wait_for(running, timeout=3)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert provider.summary_closed.is_set()
    assert len(provider.answer_calls) == answer_count
    assert result.run.status == result.assistant.status == "failed"
    assert result.run.error_code == "access_revoked"
    assert result.assistant.content == ""
    assert result.reservation.status == "released"
    assert result.reservation.usage_basis == "waived"
    assert result.reservation.input_tokens == result.reservation.output_tokens == 0
    assert len(summaries) == 1
    assert summaries[0].status == "failed"
    assert summaries[0].error_code == "access_revoked"
    assert summaries[0].content is None
    assert summaries[0].usage_basis == "provider"
    assert summaries[0].output_tokens == 12
    assert await checkpoint(harness, harness.member) == (None, 0)
    # 계정·소속 권한이 철회된 후에는 사용자 API 대신 격리 DB의 예산을 직접 비교한다.
    async with harness.database.session() as session:
        budget = await session.get(TokenBudget, admitted.reservation.budget_id)
        assert budget.used_tokens == before_used
        assert budget.reserved_tokens == 0
