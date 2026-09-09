"""답변 준비와 생성에서 네트워크 대기를 중단하는 공통 규칙."""

import asyncio


class GenerationCancelled(Exception):
    """사용자가 답변 준비 또는 생성 중에 현재 요청을 중단했다."""


async def cancellable(awaitable, cancellation: asyncio.Task, *, completed_first: bool = False):
    """토큰 계산·스트림 읽기 중에도 중단 신호가 오면 진행 중인 네트워크 요청을 닫는다."""
    task = asyncio.create_task(awaitable)
    try:
        completed, _ = await asyncio.wait((task, cancellation), return_when=asyncio.FIRST_COMPLETED)
        if completed_first and task in completed:
            return task.result()
        if cancellation in completed:
            cancellation.result()
            raise GenerationCancelled
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
