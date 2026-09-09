"""결제 연동 전의 내부 토큰 한도·완료 후 차감·기존 예약 정산 계약.

인증된 호출자가 전달한 사용자만 처리하며 공개 HTTP API에 직접 연결하지 않는다.
모든 쓰기는 flush만 수행한다. commit과 DB 오류 이후 rollback은 호출자의 책임이다.
system 역할은 한도 관리와 자신의 한도 면제만 허용하며 타인의 채팅 권한을 바꾸지 않는다.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import TokenBudget, TokenReservation, UsagePlan, User
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, RepositoryError
from backend.app.repositories.core import identifier, logged, required_text

MAX_TOKENS = 2**63 - 1


class QuotaExceeded(RepositoryError):
    """현재 기간에 사용할 수 있는 토큰이 부족하다."""


@dataclass(frozen=True, slots=True)
class TokenBalance:
    """일반 사용자는 현재 예산, system 사용자는 모든 예약의 누적 사용량을 반환한다."""

    user_id: UUID
    unlimited: bool
    token_limit: int | None
    used_tokens: int
    reserved_tokens: int
    remaining_tokens: int | None
    budget_id: UUID | None
    starts_at: datetime | None
    ends_at: datetime | None
    budget_source: str | None = None
    plan_name: str | None = None


def utc_now() -> datetime:
    """사용자 잠금을 얻은 뒤 평가할 현재 UTC 시각."""
    return datetime.now(UTC)


def token_count(value: int, *, positive: bool = False) -> int:
    if type(value) is not int or not (1 if positive else 0) <= value <= MAX_TOKENS:
        raise InvalidInput("토큰 수는 허용 범위의 64비트 정수여야 합니다.")
    return value


def utc_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidInput("예산 기간에는 시간대가 있는 날짜와 시각이 필요합니다.")
    try:
        return value.astimezone(UTC)
    except (ValueError, OverflowError):
        raise InvalidInput("예산 기간의 날짜와 시각이 올바르지 않습니다.") from None


class TokenQuotaService:
    """사용자 행 → 예산 행 → 예약 행 순서로 잠가 예약과 정산을 직렬화한다."""

    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = identifier(actor_id)

    async def _lock_users(self, *user_ids: UUID) -> dict[UUID, User]:
        # 여러 system 관리자가 서로의 예산을 부여해도 사용자 잠금 순서를 동일하게 유지한다.
        rows = await self.session.scalars(
            select(User)
            .where(User.id.in_(set(user_ids)))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {user.id: user for user in rows}

    @staticmethod
    def _require_active(user: User | None, *, system: bool = False) -> User:
        if user is None or user.status != "active" or (system and user.platform_role != "system"):
            raise AccessDenied("이 작업을 수행할 권한이 없습니다.")
        return user

    async def _lock_actor(self, *, active: bool = True) -> User:
        users = await self._lock_users(self.actor_id)
        user = users.get(self.actor_id)
        if active:
            return self._require_active(user)
        if user is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return user

    async def _current_budget(self, now: datetime) -> TokenBudget | None:
        rows = list(
            (
                await self.session.scalars(
                    select(TokenBudget)
                    .where(
                        TokenBudget.user_id == self.actor_id,
                        TokenBudget.starts_at <= now,
                        TokenBudget.ends_at > now,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        by_source: dict[str, TokenBudget] = {}
        for budget in rows:
            if budget.source in by_source:
                raise Conflict("현재 기간에 같은 종류의 예산이 중복되어 있습니다.")
            by_source[budget.source] = budget
        # 별도 플랜이 소진되어도 무료 예산을 합산하거나 자동으로 대신 사용하지 않는다.
        return by_source.get("plan") or by_source.get("free_monthly")

    async def _find_reservation(self, request_key: str, *, lock: bool = False):
        statement = (
            select(TokenReservation)
            .where(
                TokenReservation.user_id == self.actor_id,
                TokenReservation.request_key == request_key,
            )
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def _lock_reservation(
        self, request_key: str
    ) -> tuple[TokenReservation, TokenBudget | None]:
        # 사용자 잠금 아래에서 예산 ID를 읽고, 예산을 먼저 잠근 뒤 예약을 다시 조회한다.
        found = await self._find_reservation(request_key)
        if found is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        budget_id = found.budget_id
        budget = None
        if budget_id is not None:
            budget = await self.session.scalar(
                select(TokenBudget)
                .where(TokenBudget.id == budget_id, TokenBudget.user_id == self.actor_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if budget is None:
                raise Conflict("예약 예산을 확인할 수 없습니다.")
        reservation = await self._find_reservation(request_key, lock=True)
        if reservation is None or reservation.budget_id != budget_id:
            raise Conflict("예약 정보가 변경되었습니다.")
        return reservation, budget

    async def _require_no_pending_deferred(self) -> None:
        """불명 사용량을 포함한 미정산 요청이 있으면 추가 실행으로 우회하지 못하게 한다."""
        pending = await self.session.scalar(
            select(TokenReservation.id).where(
                TokenReservation.user_id == self.actor_id,
                TokenReservation.charge_mode == "deferred",
                TokenReservation.status == "reserved",
            )
        )
        if pending is not None:
            raise Conflict("이전 답변의 토큰 사용량 정산이 끝나야 새 답변을 생성할 수 있습니다.")

    @logged
    async def create_plan(self, *, code: str, name: str, token_limit: int) -> UsagePlan:
        """활성 system 사용자만 새 요금제의 토큰 한도를 정의한다."""
        code = required_text(code, 100, "요금제 코드")
        name = required_text(name, 200, "요금제 이름")
        token_limit = token_count(token_limit)
        self._require_active(await self._lock_actor(), system=True)
        if (
            await self.session.scalar(select(UsagePlan.id).where(UsagePlan.code == code))
            is not None
        ):
            raise Conflict("이미 사용 중인 요금제 코드입니다.")
        plan = UsagePlan(code=code, name=name, token_limit=token_limit)
        self.session.add(plan)
        await self.session.flush()
        return plan

    @logged
    async def grant_budget(
        self,
        *,
        user_id: UUID,
        grant_key: str,
        starts_at: datetime,
        ends_at: datetime,
        plan_id: UUID | None = None,
        token_limit: int | None = None,
    ) -> TokenBudget:
        """별도 플랜끼리 기간이 겹치지 않는 예산을 부여하고 한도는 부여 시점 값으로 고정한다.

        같은 요청 키와 정규화된 입력은 요금제가 나중에 변경되어도 기존 예산을 반환한다.
        비활성 대상 계정에도 예산을 부여할 수 있지만 사용 재개 전에는 예약할 수 없다.
        """
        user_id = identifier(user_id)
        grant_key = required_text(grant_key, 200, "예산 요청 키")
        starts_at, ends_at = utc_timestamp(starts_at), utc_timestamp(ends_at)
        if starts_at >= ends_at:
            raise InvalidInput("예산 종료 시각은 시작 시각보다 늦어야 합니다.")
        if plan_id is not None:
            plan_id = identifier(plan_id)
        if token_limit is not None:
            token_limit = token_count(token_limit)
        elif plan_id is None:
            raise InvalidInput("요금제 없는 예산에는 토큰 한도가 필요합니다.")
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "user_id": str(user_id),
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "plan_id": str(plan_id) if plan_id is not None else None,
                    "token_limit": token_limit,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        users = await self._lock_users(self.actor_id, user_id)
        self._require_active(users.get(self.actor_id), system=True)
        if user_id not in users:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        existing = await self.session.scalar(
            select(TokenBudget)
            .where(TokenBudget.grant_key == grant_key)
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if existing.grant_fingerprint != fingerprint:
                raise Conflict("같은 예산 요청 키에 다른 내용을 사용할 수 없습니다.")
            return existing

        if plan_id is not None:
            plan = await self.session.scalar(
                select(UsagePlan)
                .where(UsagePlan.id == plan_id, UsagePlan.is_active.is_(True))
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
            if plan is None:
                raise InvalidInput("사용할 수 없는 요금제입니다.")
            if token_limit is None:
                token_limit = plan.token_limit

        overlap = await self.session.scalar(
            select(TokenBudget.id).where(
                TokenBudget.user_id == user_id,
                TokenBudget.source == "plan",
                TokenBudget.starts_at < ends_at,
                TokenBudget.ends_at > starts_at,
            )
        )
        if overlap is not None:
            raise Conflict("기존 예산과 사용 기간이 겹칩니다.")
        budget = TokenBudget(
            user_id=user_id,
            source="plan",
            plan_id=plan_id,
            grant_key=grant_key,
            grant_fingerprint=fingerprint,
            starts_at=starts_at,
            ends_at=ends_at,
            token_limit=token_limit,
        )
        self.session.add(budget)
        await self.session.flush()
        return budget

    @logged
    async def get_balance(self) -> TokenBalance:
        """자신의 현재 잔여량만 조회한다. system의 무제한 잔여량은 None으로 표시한다."""
        user = await self._lock_actor()
        if user.platform_role == "system":
            totals = (
                await self.session.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        TokenReservation.status == "settled",
                                        TokenReservation.input_tokens
                                        + TokenReservation.output_tokens,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                case(
                                    (
                                        and_(
                                            TokenReservation.status == "reserved",
                                            TokenReservation.charge_mode == "reserved",
                                        ),
                                        TokenReservation.reserved_tokens,
                                    ),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    ).where(TokenReservation.user_id == self.actor_id)
                )
            ).one()
            return TokenBalance(
                self.actor_id, True, None, int(totals[0]), int(totals[1]), None, None, None, None
            )
        budget = await self._current_budget(utc_now())
        if budget is None:
            return TokenBalance(self.actor_id, False, 0, 0, 0, 0, None, None, None)
        plan_name = (
            await self.session.scalar(select(UsagePlan.name).where(UsagePlan.id == budget.plan_id))
            if budget.plan_id is not None
            else None
        )
        return TokenBalance(
            user_id=self.actor_id,
            unlimited=False,
            token_limit=budget.token_limit,
            used_tokens=budget.used_tokens,
            reserved_tokens=budget.reserved_tokens,
            remaining_tokens=budget.token_limit - budget.used_tokens - budget.reserved_tokens,
            budget_id=budget.id,
            starts_at=budget.starts_at,
            ends_at=budget.ends_at,
            budget_source=budget.source,
            plan_name=plan_name,
        )

    @logged
    async def reserve(self, *, request_key: str, reserved_tokens: int) -> TokenReservation:
        """입출력 전체의 최대 토큰을 예약하고 같은 요청에는 기존 회계 기록을 반환한다.

        기존 기록 반환은 모델 재실행 권한이나 작업 선점을 의미하지 않는다. reserved도
        이미 실행 중일 수 있으므로 생성 작업의 요청 키 고유성과 선점은 별도로 보장해야 한다.
        settled/released 상태를 포함해 같은 요청을 다시 차감하거나 예약하지 않는다.
        """
        request_key = required_text(request_key, 200, "예약 요청 키")
        reserved_tokens = token_count(reserved_tokens, positive=True)
        user = await self._lock_actor()
        existing = await self._find_reservation(request_key)
        if existing is not None:
            if existing.reserved_tokens != reserved_tokens or existing.charge_mode != "reserved":
                raise Conflict("같은 예약 요청 키에 다른 내용을 사용할 수 없습니다.")
            return existing

        await self._require_no_pending_deferred()

        quota_exempt = user.platform_role == "system"
        budget = None if quota_exempt else await self._current_budget(utc_now())
        if not quota_exempt:
            if budget is None or reserved_tokens > (
                budget.token_limit - budget.used_tokens - budget.reserved_tokens
            ):
                raise QuotaExceeded("현재 사용할 수 있는 토큰이 부족합니다.")
            budget.reserved_tokens += reserved_tokens
        reservation = TokenReservation(
            user_id=self.actor_id,
            budget_id=budget.id if budget is not None else None,
            request_key=request_key,
            quota_exempt=quota_exempt,
            charge_mode="reserved",
            reserved_tokens=reserved_tokens,
        )
        self.session.add(reservation)
        await self.session.flush()
        return reservation

    @logged
    async def begin_deferred(self, *, request_key: str, authorized_tokens: int) -> TokenReservation:
        """예산을 차감하거나 예약하지 않고 답변의 허용 사용량과 원래 예산을 기록한다.

        같은 사용자에게 미정산 완료 후 차감 요청은 하나만 허용한다. 이 기록은 모델
        재실행 권한이 아니며, 동일 요청 키 재시도에는 기존 기록을 그대로 반환한다.
        """
        request_key = required_text(request_key, 200, "사용량 요청 키")
        authorized_tokens = token_count(authorized_tokens, positive=True)
        user = await self._lock_actor()
        existing = await self._find_reservation(request_key)
        if existing is not None:
            if existing.charge_mode != "deferred" or existing.reserved_tokens != authorized_tokens:
                raise Conflict("같은 사용량 요청 키에 다른 내용을 사용할 수 없습니다.")
            return existing

        await self._require_no_pending_deferred()
        quota_exempt = user.platform_role == "system"
        budget = None if quota_exempt else await self._current_budget(utc_now())
        if not quota_exempt and (
            budget is None
            or authorized_tokens > budget.token_limit - budget.used_tokens - budget.reserved_tokens
        ):
            raise QuotaExceeded("현재 사용할 수 있는 토큰이 부족합니다.")
        reservation = TokenReservation(
            user_id=self.actor_id,
            budget_id=budget.id if budget is not None else None,
            request_key=request_key,
            quota_exempt=quota_exempt,
            charge_mode="deferred",
            reserved_tokens=authorized_tokens,
        )
        self.session.add(reservation)
        await self.session.flush()
        return reservation

    @logged
    async def settle(
        self,
        *,
        request_key: str,
        input_tokens: int,
        output_tokens: int,
        usage_basis: str = "provider",
    ) -> TokenReservation:
        """신뢰된 내부 작업자가 청구 사용량과 산정 기준을 확정한다.

        비활성 계정의 기존 예약도 정산하며, 면제 기준은 실제 청구량이 0일 때만 허용한다.
        """
        request_key = required_text(request_key, 200, "예약 요청 키")
        input_tokens, output_tokens = token_count(input_tokens), token_count(output_tokens)
        actual_tokens = token_count(input_tokens + output_tokens)
        if not isinstance(usage_basis, str) or usage_basis not in {
            "provider",
            "received",
            "waived",
        }:
            raise InvalidInput("지원하지 않는 토큰 정산 기준입니다.")
        if usage_basis == "waived" and actual_tokens != 0:
            raise InvalidInput("면제 정산에는 사용량을 청구할 수 없습니다.")
        await self._lock_actor(active=False)
        reservation, budget = await self._lock_reservation(request_key)
        if reservation.status == "settled":
            if (
                reservation.input_tokens,
                reservation.output_tokens,
                reservation.usage_basis,
            ) != (
                input_tokens,
                output_tokens,
                usage_basis,
            ):
                raise Conflict("이미 다른 사용량 또는 기준으로 정산한 예약입니다.")
            return reservation
        if reservation.status != "reserved":
            raise Conflict("취소한 예약을 정산할 수 없습니다.")
        if actual_tokens > reservation.reserved_tokens:
            raise InvalidInput("실제 사용량이 허용한 토큰 수를 초과했습니다.")
        if budget is not None:
            if reservation.charge_mode == "deferred":
                self._validate_deferred_charge(budget, actual_tokens)
            else:
                self._validate_refund(budget, reservation, actual_tokens)
                budget.reserved_tokens -= reservation.reserved_tokens
            budget.used_tokens += actual_tokens
        reservation.status = "settled"
        reservation.input_tokens = input_tokens
        reservation.output_tokens = output_tokens
        reservation.usage_basis = usage_basis
        reservation.completed_at = utc_now()
        await self.session.flush()
        return reservation

    @logged
    async def release(self, *, request_key: str) -> TokenReservation:
        """미사용 요청을 종료하며 사전 예약 방식에만 예약량을 반환한다."""
        request_key = required_text(request_key, 200, "예약 요청 키")
        await self._lock_actor(active=False)
        reservation, budget = await self._lock_reservation(request_key)
        if reservation.status == "released":
            return reservation
        if reservation.status != "reserved":
            raise Conflict("이미 정산한 예약을 취소할 수 없습니다.")
        if budget is not None and reservation.charge_mode == "reserved":
            self._validate_refund(budget, reservation, 0)
            budget.reserved_tokens -= reservation.reserved_tokens
        reservation.status = "released"
        reservation.input_tokens = 0
        reservation.output_tokens = 0
        reservation.usage_basis = "waived"
        reservation.completed_at = utc_now()
        await self.session.flush()
        return reservation

    @staticmethod
    def _validate_deferred_charge(budget: TokenBudget, actual_tokens: int) -> None:
        if (
            budget.used_tokens + actual_tokens > MAX_TOKENS
            or budget.used_tokens + budget.reserved_tokens + actual_tokens > budget.token_limit
        ):
            raise Conflict("확정 사용량이 원래 예산의 잔여 토큰을 초과합니다.")

    @staticmethod
    def _validate_refund(
        budget: TokenBudget, reservation: TokenReservation, actual_tokens: int
    ) -> None:
        if (
            budget.reserved_tokens < reservation.reserved_tokens
            or budget.used_tokens + actual_tokens > MAX_TOKENS
            or budget.used_tokens
            + actual_tokens
            + budget.reserved_tokens
            - reservation.reserved_tokens
            > budget.token_limit
        ):
            raise Conflict("예산 사용량과 예약 정보가 일치하지 않습니다.")
