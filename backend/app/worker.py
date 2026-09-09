"""독립 생성 실행자 진입점: python -m backend.app.worker."""

import asyncio
import logging
import signal

from backend.app.config import Settings, get_settings
from backend.app.db import Database
from backend.app.llm.registry import build_provider
from backend.app.runtime.worker import GenerationWorker
from backend.app.services.generations import GenerationService

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings, *, stop_event: asyncio.Event | None = None) -> None:
    """종료 신호를 받으면 모델 연결·정산·리더 잠금을 순서대로 정리한다."""
    if not settings.database_enabled:
        raise ValueError("독립 생성 실행자는 DATABASE_ENABLED=true 설정이 필요합니다.")
    database = Database(settings)
    worker = None
    stop = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM) if stop_event is None else ()
    waiting = None
    try:
        worker = GenerationWorker(GenerationService(database, build_provider(settings), settings))
        for item in signals:
            loop.add_signal_handler(item, stop.set)
        worker.start()
        waiting = asyncio.create_task(stop.wait(), name="worker-shutdown-signal")
        completed, _ = await asyncio.wait(
            (worker.task, waiting), return_when=asyncio.FIRST_COMPLETED
        )
        if worker.task in completed:
            await worker.task
            raise RuntimeError("생성 실행자가 예기치 않게 종료되었습니다.")
    finally:
        if waiting is not None:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        try:
            if worker is not None:
                await worker.stop()
        finally:
            await database.dispose()
            for item in signals:
                loop.remove_signal_handler(item)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker(get_settings()))
    except Exception as error:
        # 환경변수 검증·드라이버 예외에 포함될 수 있는 인증정보는 출력하지 않는다.
        logger.error("독립 생성 실행자 시작/종료 실패: %s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
