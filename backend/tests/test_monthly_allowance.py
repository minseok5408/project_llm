"""월별 무료 지급, 별도 플랜 우선순위와 예약의 원래 예산 정산을 검증한다."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from backend.app.db import Database
from backend.app.models import TokenBudget, TokenReservation, UsagePlan, User
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    create_user_with_workspace,
)
from backend.app.services import monthly_allowance as monthly_module
from backend.app.services import token_quota as quota_module
from backend.app.services.monthly_allowance import MonthlyAllowanceService, month_period
from backend.app.services.token_quota import QuotaExceeded, TokenQuotaService
from backend.tests.conftest import IsolatedPostgres, database_settings

pytestmark = pytest.mark.postgres


@dataclass
class Clock:
    now: datetime = datetime(2030, 1, 15, 12, tzinfo=UTC)


@dataclass(frozen=True)
class Accounts:
    member: UUID
    other: UUID
    system: UUID


@pytest.fixture
def clock(monkeypatch) -> Clock:
    current = Clock()
    monkeypatch.setattr(monthly_module, "utc_now", lambda: current.now)
    monkeypatch.setattr(quota_module, "utc_now", lambda: current.now)
    return current


@pytest.fixture
async def accounts(schema_database: Database, clock: Clock) -> Accounts:
    async with schema_database.session() as session:
        rows = [
            await create_user_with_workspace(
                session, email=f"{name}@example.com", display_name=f"무료 지급 테스트 {name}"
            )
            for name in ("member", "other", "system")
        ]
        rows[2].user.platform_role = "system"
        await session.commit()
        return Accounts(*(row.user.id for row in rows))


async def ensure(database: Database, user_id: UUID) -> TokenBudget | None:
    async with database.session() as session:
        budget = await MonthlyAllowanceService(session, user_id).ensure()
        await session.commit()
        return budget


async def balance(database: Database, user_id: UUID):
    async with database.session() as session:
        return await TokenQuotaService(session, user_id).get_balance()


async def reserve(database: Database, user_id: UUID, key: str, amount: int) -> TokenReservation:
    async with database.session() as session:
        reservation = await TokenQuotaService(session, user_id).reserve(
            request_key=key, reserved_tokens=amount
        )
        await session.commit()
        return reservation


async def settle(
    database: Database, user_id: UUID, key: str, input_tokens: int, output_tokens: int
):
    async with database.session() as session:
        reservation = await TokenQuotaService(session, user_id).settle(
            request_key=key, input_tokens=input_tokens, output_tokens=output_tokens
        )
        await session.commit()
        return reservation


async def grant_plan(
    database: Database,
    accounts: Accounts,
    clock: Clock,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    limit: int = 100,
) -> TokenBudget:
    async with database.session() as session:
        budget = await TokenQuotaService(session, accounts.system).grant_budget(
            user_id=accounts.member,
            grant_key=str(uuid4()),
            starts_at=starts_at or clock.now - timedelta(days=1),
            ends_at=ends_at or clock.now + timedelta(days=1),
            token_limit=limit,
        )
        await session.commit()
        return budget


async def counts(database: Database) -> tuple[int, int]:
    async with database.session() as session:
        return (
            await session.scalar(select(func.count()).select_from(TokenBudget)),
            await session.scalar(select(func.count()).select_from(UsagePlan)),
        )


async def test_member_receives_one_monthly_snapshot_and_usage_metadata(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    first = await ensure(schema_database, accounts.member)
    repeated = await ensure(schema_database, accounts.member)
    assert repeated.id == first.id
    assert first.source == "free_monthly"
    assert first.token_limit == 20_000
    assert first.used_tokens == first.reserved_tokens == 0
    assert first.starts_at == datetime(2029, 12, 31, 15, tzinfo=UTC)
    assert first.ends_at == datetime(2030, 1, 31, 15, tzinfo=UTC)
    assert str(accounts.member) in first.grant_key
    assert first.grant_key.endswith("2030-01")
    assert len(first.grant_fingerprint) == 64
    assert await counts(schema_database) == (1, 1)
    current = await balance(schema_database, accounts.member)
    assert current.budget_id == first.id
    assert current.budget_source == "free_monthly"
    assert current.plan_name == "무료"
    assert current.remaining_tokens == 20_000
    assert current.unlimited is False
    async with schema_database.session() as session:
        assert (await session.get(User, accounts.member)).platform_role == "member"
        plan = await session.get(UsagePlan, first.plan_id)
        assert (plan.code, plan.name, plan.token_limit) == ("free-monthly", "무료", 20_000)


async def test_system_remains_exempt_without_creating_free_budget_or_plan(
    schema_database: Database,
    accounts: Accounts,
) -> None:
    assert await ensure(schema_database, accounts.system) is None
    assert await counts(schema_database) == (0, 0)
    current = await balance(schema_database, accounts.system)
    assert current.unlimited is True
    assert current.budget_source is current.plan_name is None


@pytest.mark.parametrize("missing", [False, True])
async def test_disabled_or_missing_user_cannot_receive_allowance(
    schema_database: Database,
    accounts: Accounts,
    missing: bool,
) -> None:
    user_id = uuid4() if missing else accounts.member
    if not missing:
        async with schema_database.session() as session:
            await session.execute(update(User).where(User.id == user_id).values(status="disabled"))
            await session.commit()
    with pytest.raises(AccessDenied):
        await ensure(schema_database, user_id)
    assert await counts(schema_database) == (0, 0)


async def test_receiving_free_allowance_does_not_grant_plan_administration_permissions(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    await ensure(schema_database, accounts.member)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, accounts.member)
        with pytest.raises(AccessDenied):
            await service.create_plan(code="unauthorized", name="임의 요금제", token_limit=999_999)
        with pytest.raises(AccessDenied):
            await service.grant_budget(
                user_id=accounts.member,
                grant_key="unauthorized-grant",
                starts_at=clock.now,
                ends_at=clock.now + timedelta(days=1),
                token_limit=999_999,
            )
    assert await counts(schema_database) == (1, 1)


@pytest.mark.parametrize("same_user", [False, True])
async def test_concurrent_allowance_calls_use_one_plan_and_one_budget_per_user_month(
    schema_database: Database,
    postgres: IsolatedPostgres,
    accounts: Accounts,
    same_user: bool,
) -> None:
    other_database = Database(database_settings(postgres))
    ready = asyncio.Barrier(2)

    async def together(database, user_id):
        await ready.wait()
        return await ensure(database, user_id)

    try:
        budgets = await asyncio.wait_for(
            asyncio.gather(
                together(schema_database, accounts.member),
                together(other_database, accounts.member if same_user else accounts.other),
            ),
            timeout=10,
        )
    finally:
        await other_database.dispose()
    assert len({budget.id for budget in budgets}) == (1 if same_user else 2)
    assert len({budget.plan_id for budget in budgets}) == 1
    assert await counts(schema_database) == (1 if same_user else 2, 1)


async def test_consumed_allowance_is_not_refilled_by_repeated_ensure(
    schema_database: Database,
    accounts: Accounts,
) -> None:
    initial = await ensure(schema_database, accounts.member)
    await reserve(schema_database, accounts.member, "use-entire-month", 20_000)
    await settle(schema_database, accounts.member, "use-entire-month", 12_000, 8_000)
    repeated = await ensure(schema_database, accounts.member)
    assert repeated.id == initial.id
    assert repeated.used_tokens == 20_000
    assert repeated.token_limit == 20_000
    with pytest.raises(QuotaExceeded):
        await reserve(schema_database, accounts.member, "cannot-refill", 1)
    assert (await balance(schema_database, accounts.member)).remaining_tokens == 0
    assert await counts(schema_database) == (1, 1)


@pytest.mark.parametrize(
    ("at", "month", "start", "end"),
    [
        (
            "2029-12-31T14:59:59+00:00",
            "2029-12",
            "2029-11-30T15:00:00+00:00",
            "2029-12-31T15:00:00+00:00",
        ),
        (
            "2029-12-31T15:00:00+00:00",
            "2030-01",
            "2029-12-31T15:00:00+00:00",
            "2030-01-31T15:00:00+00:00",
        ),
        (
            "2032-02-29T14:59:59+00:00",
            "2032-02",
            "2032-01-31T15:00:00+00:00",
            "2032-02-29T15:00:00+00:00",
        ),
    ],
)
def test_month_period_uses_seoul_calendar_including_year_end_and_leap_february(
    at, month, start, end
) -> None:
    assert month_period(datetime.fromisoformat(at)) == (
        month,
        datetime.fromisoformat(start),
        datetime.fromisoformat(end),
    )


async def test_month_boundary_replaces_allowance_without_carrying_unused_tokens(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    clock.now = datetime(2029, 12, 31, 14, 59, 59, tzinfo=UTC)
    december = await ensure(schema_database, accounts.member)
    await reserve(schema_database, accounts.member, "december-used", 1_000)
    await settle(schema_database, accounts.member, "december-used", 200, 100)
    assert (await balance(schema_database, accounts.member)).remaining_tokens == 19_700
    clock.now = december.ends_at
    january = await ensure(schema_database, accounts.member)
    assert january.id != december.id
    assert january.starts_at == december.ends_at
    assert january.used_tokens == january.reserved_tokens == 0
    current = await balance(schema_database, accounts.member)
    assert current.budget_id == january.id
    assert current.remaining_tokens == 20_000
    assert await counts(schema_database) == (2, 1)


async def test_late_settlement_updates_original_month_and_does_not_charge_current_month(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    old = await ensure(schema_database, accounts.member)
    reservation = await reserve(schema_database, accounts.member, "previous-month", 1_000)
    clock.now = old.ends_at
    current = await ensure(schema_database, accounts.member)
    settled = await settle(schema_database, accounts.member, "previous-month", 200, 100)
    assert settled.id == reservation.id
    assert settled.budget_id == old.id
    assert settled.status == "settled"
    current_balance = await balance(schema_database, accounts.member)
    assert current_balance.budget_id == current.id
    assert current_balance.remaining_tokens == 20_000
    assert current_balance.used_tokens == current_balance.reserved_tokens == 0
    async with schema_database.session() as session:
        original = await session.get(TokenBudget, old.id)
        assert original.used_tokens == 300
        assert original.reserved_tokens == 0


async def test_plan_priority_prevents_free_fallback_and_expiry_restores_remaining_free_tokens(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    free = await ensure(schema_database, accounts.member)
    free_reservation = await reserve(schema_database, accounts.member, "free-before-plan", 1_000)
    plan = await grant_plan(schema_database, accounts, clock, limit=100)
    assert plan.source == "plan"
    assert (await ensure(schema_database, accounts.member)).id == plan.id
    current = await balance(schema_database, accounts.member)
    assert current.budget_source == "plan"
    assert current.token_limit == current.remaining_tokens == 100
    # 새 플랜이 활성화되어도 기존 무료 예약은 원래 무료 예산에 정산한다.
    old_settled = await settle(schema_database, accounts.member, "free-before-plan", 200, 100)
    assert old_settled.id == free_reservation.id
    assert old_settled.budget_id == free.id
    assert (await balance(schema_database, accounts.member)).remaining_tokens == 100
    await reserve(schema_database, accounts.member, "plan-only", 100)
    await settle(schema_database, accounts.member, "plan-only", 60, 40)
    assert (await ensure(schema_database, accounts.member)).id == plan.id
    assert (await balance(schema_database, accounts.member)).remaining_tokens == 0
    with pytest.raises(QuotaExceeded):
        await reserve(schema_database, accounts.member, "no-free-fallback", 1)
    with pytest.raises(Conflict):
        await grant_plan(schema_database, accounts, clock, limit=200)

    clock.now = plan.ends_at
    restored = await ensure(schema_database, accounts.member)
    assert restored.id == free.id
    assert restored.starts_at == free.starts_at
    assert restored.ends_at == free.ends_at
    assert restored.token_limit == 20_000
    current = await balance(schema_database, accounts.member)
    assert current.budget_source == "free_monthly"
    assert current.used_tokens == 300
    assert current.reserved_tokens == 0
    assert current.remaining_tokens == 19_700


async def test_existing_active_plan_is_preserved_and_free_is_created_only_after_expiry(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    plan = await grant_plan(schema_database, accounts, clock)
    assert (await ensure(schema_database, accounts.member)).id == plan.id
    assert await counts(schema_database) == (1, 0)
    clock.now = plan.ends_at
    free = await ensure(schema_database, accounts.member)
    assert free.source == "free_monthly"
    assert free.token_limit == 20_000
    assert free.id != plan.id
    assert await counts(schema_database) == (2, 1)


async def test_scheduled_plan_does_not_prevent_current_month_free_allowance(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    scheduled = await grant_plan(
        schema_database,
        accounts,
        clock,
        starts_at=clock.now + timedelta(days=1),
        ends_at=clock.now + timedelta(days=2),
    )
    free = await ensure(schema_database, accounts.member)
    assert free.source == "free_monthly"
    clock.now = scheduled.starts_at
    assert (await ensure(schema_database, accounts.member)).id == scheduled.id
    clock.now = scheduled.ends_at
    assert (await ensure(schema_database, accounts.member)).id == free.id


@pytest.mark.parametrize("source", ["plan", "free_monthly"])
async def test_duplicate_budgets_of_the_same_source_fail_closed_even_with_plan_priority(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
    source: str,
) -> None:
    free = await ensure(schema_database, accounts.member)
    plan = await grant_plan(schema_database, accounts, clock)
    original = plan if source == "plan" else free
    async with schema_database.session() as session:
        session.add(
            TokenBudget(
                user_id=accounts.member,
                source=source,
                plan_id=original.plan_id,
                grant_key=str(uuid4()),
                grant_fingerprint="0" * 64,
                starts_at=original.starts_at,
                ends_at=original.ends_at,
                token_limit=100,
            )
        )
        await session.commit()
    with pytest.raises(Conflict):
        await balance(schema_database, accounts.member)
    with pytest.raises(Conflict):
        await ensure(schema_database, accounts.member)


async def test_rollback_discards_both_seed_plan_and_allowance_and_retry_grants_once(
    schema_database: Database,
    accounts: Accounts,
) -> None:
    async with schema_database.session() as session:
        await MonthlyAllowanceService(session, accounts.member).ensure()
        await session.rollback()
    assert await counts(schema_database) == (0, 0)
    await ensure(schema_database, accounts.member)
    await ensure(schema_database, accounts.member)
    assert await counts(schema_database) == (1, 1)


async def test_existing_policy_is_not_overwritten_and_changes_only_affect_new_snapshots(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    async with schema_database.session() as session:
        plan = await TokenQuotaService(session, accounts.system).create_plan(
            code="free-monthly", name="운영 무료", token_limit=1_234
        )
        await session.commit()
    first = await ensure(schema_database, accounts.member)
    assert first.plan_id == plan.id
    assert first.token_limit == 1_234
    async with schema_database.session() as session:
        await session.execute(
            update(UsagePlan).where(UsagePlan.id == plan.id).values(token_limit=4_567)
        )
        await session.commit()
    repeated = await ensure(schema_database, accounts.member)
    assert repeated.id == first.id
    assert repeated.token_limit == 1_234
    other = await ensure(schema_database, accounts.other)
    assert other.token_limit == 4_567
    clock.now = first.ends_at
    renewed = await ensure(schema_database, accounts.member)
    assert renewed.token_limit == 4_567
    assert renewed.id != first.id


async def test_inactive_free_plan_keeps_existing_snapshot_but_rejects_new_grants(
    schema_database: Database,
    accounts: Accounts,
    clock: Clock,
) -> None:
    first = await ensure(schema_database, accounts.member)
    async with schema_database.session() as session:
        await session.execute(
            update(UsagePlan).where(UsagePlan.id == first.plan_id).values(is_active=False)
        )
        await session.commit()
    assert (await ensure(schema_database, accounts.member)).id == first.id
    with pytest.raises(InvalidInput):
        await ensure(schema_database, accounts.other)
    clock.now = first.ends_at
    with pytest.raises(InvalidInput):
        await ensure(schema_database, accounts.member)
    assert await counts(schema_database) == (1, 1)
