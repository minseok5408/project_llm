"""서버가 정한 월별 무료 예산을 지급하며 외부 입력으로 지급 정책을 바꾸지 않는다."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import TokenBudget, UsagePlan
from backend.app.repositories import Conflict, InvalidInput
from backend.app.repositories.operations import logged
from backend.app.repositories.validation import identifier
from backend.app.services.token_quota import TokenQuotaService

FREE_PLAN_CODE = "free-monthly"
FREE_PLAN_NAME = "무료"
FREE_TOKEN_LIMIT = 20_000
SEOUL = ZoneInfo("Asia/Seoul")


def utc_now() -> datetime:
    """사용자 잠금을 얻은 뒤 지급할 달을 평가한다."""
    return datetime.now(UTC)


def month_period(at: datetime) -> tuple[str, datetime, datetime]:
    """서울 달력의 월 식별자와 시작 포함·종료 제외 UTC 범위를 반환한다."""
    local = at.astimezone(SEOUL)
    start = datetime(local.year, local.month, 1, tzinfo=SEOUL)
    end = (
        datetime(local.year + 1, 1, 1, tzinfo=SEOUL)
        if local.month == 12
        else datetime(local.year, local.month + 1, 1, tzinfo=SEOUL)
    )
    return start.strftime("%Y-%m"), start.astimezone(UTC), end.astimezone(UTC)


class MonthlyAllowanceService:
    """인증된 자기 계정의 무료 예산만 보장한다. 커밋은 호출자가 수행한다.

    별도 플랜 지급 권한은 TokenQuotaService에 유지한다. 이 서비스는 클라이언트의
    한도·기간·플랜 입력을 받지 않으며 system 역할을 가진 대리 계정을 만들지 않는다.
    """

    def __init__(self, session: AsyncSession, user_id: UUID) -> None:
        self.session = session
        self.user_id = identifier(user_id)

    @logged
    async def ensure(self) -> TokenBudget | None:
        quota = TokenQuotaService(self.session, self.user_id)
        user = await quota._lock_actor()
        if user.platform_role == "system":
            return None

        current_time = utc_now()
        current = await quota._current_budget(current_time)
        if current is not None and current.source == "plan":
            return current

        month, starts_at, ends_at = month_period(current_time)
        # 정책 한도나 표시 이름이 바뀌어도 같은 사용자에게 같은 달 예산을 다시 주지 않는다.
        grant_key = f"free-monthly:{self.user_id}:{month}"
        existing = await self.session.scalar(
            select(TokenBudget)
            .where(TokenBudget.grant_key == grant_key)
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if existing.user_id != self.user_id or existing.source != "free_monthly":
                raise Conflict("월별 무료 예산의 지급 기록을 확인할 수 없습니다.")
            return existing

        overlap = await self.session.scalar(
            select(TokenBudget.id).where(
                TokenBudget.user_id == self.user_id,
                TokenBudget.source == "free_monthly",
                TokenBudget.starts_at < ends_at,
                TokenBudget.ends_at > starts_at,
            )
        )
        if overlap is not None:
            raise Conflict("이번 달 무료 예산과 사용 기간이 겹치는 지급 기록이 있습니다.")

        # 동시에 가입한 사용자들도 상품을 한 번만 만들고 기존 운영 정책은 덮어쓰지 않는다.
        await self.session.execute(
            insert(UsagePlan)
            .values(
                code=FREE_PLAN_CODE,
                name=FREE_PLAN_NAME,
                token_limit=FREE_TOKEN_LIMIT,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=[UsagePlan.code])
        )
        plan = await self.session.scalar(
            select(UsagePlan)
            .where(UsagePlan.code == FREE_PLAN_CODE)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if plan is None or not plan.is_active:
            raise InvalidInput("현재 무료 요금제를 사용할 수 없습니다.")

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "source": "free_monthly",
                    "user_id": str(self.user_id),
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "plan_id": str(plan.id),
                    "token_limit": plan.token_limit,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        budget = TokenBudget(
            user_id=self.user_id,
            source="free_monthly",
            plan_id=plan.id,
            grant_key=grant_key,
            grant_fingerprint=fingerprint,
            starts_at=starts_at,
            ends_at=ends_at,
            token_limit=plan.token_limit,
        )
        self.session.add(budget)
        await self.session.flush()
        return budget
