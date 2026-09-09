import asyncio
import json
import logging
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.llm.protocol import ChatProvider
from backend.app.models import GenerationRun, WorkerHeartbeat

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])
CheckStatus = Literal["ready", "unavailable", "disabled"]


@router.get("/health", include_in_schema=False)
@router.get("/health/live")
async def live() -> dict[str, str]:
    """DB나 모델에 연결하지 않고 프로세스 생존 상태를 확인한다."""
    return {"status": "ok"}


@router.get("/health/worker", include_in_schema=False)
async def worker_status(request: Request) -> JSONResponse:
    """읽기 전용 관측이며 오래된 heartbeat를 이유로 작업을 재실행하지 않는다."""
    database: Database | None = request.app.state.database
    headers = {"Cache-Control": "no-store"}
    if database is None:
        return JSONResponse({"status": "disabled"}, headers=headers)
    try:
        async with asyncio.timeout(request.app.state.settings.database_health_timeout_seconds):
            async with database.session() as session:
                heartbeat = await session.get(WorkerHeartbeat, "generation")
                database_now = await session.scalar(select(func.clock_timestamp()))
                counts = dict(
                    (
                        await session.execute(
                            select(GenerationRun.status, func.count())
                            .where(GenerationRun.status.in_(("queued", "running")))
                            .group_by(GenerationRun.status)
                        )
                    ).all()
                )
                alive = (
                    heartbeat is not None
                    and heartbeat.stopped_at is None
                    and 0 <= (database_now - heartbeat.heartbeat_at).total_seconds() <= 10
                )
                content = {
                    "status": "ready" if alive else "unavailable",
                    "state": (
                        ("busy" if counts.get("running", 0) else "idle") if alive else "offline"
                    ),
                    "queued": counts.get("queued", 0),
                    "running": counts.get("running", 0),
                }
        return JSONResponse(content, status_code=200 if alive else 503, headers=headers)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503, headers=headers)


@router.get("/health/ready", responses={503: {"description": "A dependency is unavailable"}})
async def ready(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    database: Database | None = request.app.state.database
    provider: ChatProvider = request.app.state.provider
    started = perf_counter()

    async def database_status() -> CheckStatus:
        if database is None:
            return "disabled"
        try:
            async with asyncio.timeout(settings.database_health_timeout_seconds):
                await database.check_connection()
            return "ready"
        except Exception:
            # 드라이버 예외에는 인증정보, 호스트, SQL 매개변수가 포함될 수 있다.
            return "unavailable"

    async def model_status() -> CheckStatus:
        try:
            # 자체 상태 확인 시간 제한이 없는 공급자에도 제한을 적용한다.
            async with asyncio.timeout(3):
                result = await provider.status()
            return "ready" if result.ready else "unavailable"
        except Exception:
            return "unavailable"

    database_check, model_check = await asyncio.gather(database_status(), model_status())
    checks = {"database": database_check, "model": model_check}
    is_ready = "unavailable" not in checks.values()
    http_status = 200 if is_ready else 503
    # 허용된 필드만 기록하여 의존성 오류 상세와 연결 URL이 로그에 남지 않게 한다.
    logger.log(
        logging.INFO if is_ready else logging.WARNING,
        json.dumps(
            {
                "event": "readiness_check",
                "status_code": http_status,
                "checks": checks,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
            separators=(",", ":"),
        ),
    )
    return JSONResponse(
        status_code=http_status,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
        headers={"Cache-Control": "no-store"},
    )
