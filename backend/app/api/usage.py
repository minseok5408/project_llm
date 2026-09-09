"""인증된 사용자 자신의 현재 토큰 사용량과 예약량을 제공한다."""

from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.api.auth import CurrentAuth
from backend.app.api.conversations import EmptyPayload, data_session, private_json, read_query
from backend.app.services.monthly_allowance import MonthlyAllowanceService
from backend.app.services.token_quota import TokenQuotaService

router = APIRouter(prefix="/api/v1", tags=["usage"])


@router.get("/usage")
async def get_usage(request: Request, auth: CurrentAuth) -> JSONResponse:
    read_query(request, EmptyPayload)
    async with data_session(request) as session:
        await MonthlyAllowanceService(session, auth.user.id).ensure()
        balance = await TokenQuotaService(session, auth.user.id).get_balance()
        await session.commit()
        return private_json(asdict(balance))
