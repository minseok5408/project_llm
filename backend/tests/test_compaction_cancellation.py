"""요약 읽기의 종료·중단 경합에서 확정 사용량과 작업 정리를 보존한다."""

import asyncio

import pytest

from backend.app.llm.protocol import ProviderDelta
from backend.app.runtime.cancellation import GenerationCancelled, cancellable


@pytest.mark.asyncio
async def test_ready_final_delta_is_not_lost_when_cancellation_is_also_ready():
    async def complete():
        return ProviderDelta(input_tokens=700, output_tokens=12, final=True, finish_reason="stop")

    cancellation = asyncio.create_task(asyncio.sleep(0))
    await cancellation
    delta = await cancellable(complete(), cancellation, completed_first=True)
    assert delta.final and (delta.input_tokens, delta.output_tokens) == (700, 12)


@pytest.mark.asyncio
async def test_cancel_closes_pending_count_request():
    closed = asyncio.Event()

    async def count():
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    cancellation = asyncio.create_task(asyncio.sleep(0))
    with pytest.raises(GenerationCancelled):
        await cancellable(count(), cancellation)
    assert closed.is_set()
