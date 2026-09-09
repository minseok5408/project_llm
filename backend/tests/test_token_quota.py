"""실제 PostgreSQL에서 사용자별 토큰 예산과 시스템 계정의 면제를 검증한다."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from backend.app.db import Database
from backend.app.models import (
    TokenBudget,
    TokenReservation,
    UsagePlan,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.repositories import (
    AccessDenied,
    Conflict,
    InvalidInput,
    Repository,
    create_user_with_workspace,
)
from backend.app.services import QuotaExceeded, TokenQuotaService
from backend.app.services import token_quota as quota_module
from backend.tests.conftest import IsolatedPostgres, database_settings, run_alembic, version_rows

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]
NOW = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)


@dataclass
class Actors:
    system: UUID
    member: UUID
    other: UUID
    workspace: UUID


@pytest.fixture(autouse=True)
def quota_clock(monkeypatch):
    # SQL과 잠금은 실제 DB에서 실행하고 서비스의 현재 시각만 고정한다.
    monkeypatch.setattr(quota_module, "utc_now", lambda: NOW)


@pytest.fixture
async def actors(schema_database: Database) -> Actors:
    async with schema_database.session() as session:
        accounts = [
            await create_user_with_workspace(
                session, email=f"{name}@example.com", display_name=name
            )
            for name in ("system", "member", "other")
        ]
        accounts[0].user.platform_role = "system"
        await session.commit()
        return Actors(
            accounts[0].user.id, accounts[1].user.id, accounts[2].user.id, accounts[1].workspace.id
        )


async def grant(
    database,
    actors,
    *,
    limit=100,
    user_id=None,
    starts_at=None,
    ends_at=None,
    key=None,
    plan_id=None,
):
    async with database.session() as session:
        budget = await TokenQuotaService(session, actors.system).grant_budget(
            user_id=user_id or actors.member,
            grant_key=key or str(uuid4()),
            starts_at=starts_at or NOW - timedelta(days=1),
            ends_at=ends_at or NOW + timedelta(days=1),
            token_limit=limit,
            plan_id=plan_id,
        )
        await session.commit()
        return budget


async def balance(database, actor_id):
    async with database.session() as session:
        return await TokenQuotaService(session, actor_id).get_balance()


async def reserve(database, actor_id, key, amount):
    async with database.session() as session:
        reservation = await TokenQuotaService(session, actor_id).reserve(
            request_key=key, reserved_tokens=amount
        )
        await session.commit()
        return reservation


async def begin_deferred(database, actor_id, key, amount):
    async with database.session() as session:
        request = await TokenQuotaService(session, actor_id).begin_deferred(
            request_key=key, authorized_tokens=amount
        )
        await session.commit()
        return request


async def test_upgrade_preserves_existing_users_and_grants_no_system_role(
    postgres: IsolatedPostgres, database: Database
) -> None:
    await run_alembic(postgres, "upgrade", "0002_core_chat_schema")
    async with database.engine.begin() as connection:
        user = await connection.scalar(
            text(
                "INSERT INTO users (email,display_name) "
                "VALUES ('old@example.com','기존 사용자') RETURNING id"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO users (email,display_name) VALUES ('second@example.com','다른 사용자')"
            )
        )
        workspace = await connection.scalar(
            text(
                "INSERT INTO workspaces (name,created_by) VALUES ('기존 공간',:user) RETURNING id"
            ),
            {"user": user},
        )
        await connection.execute(
            text(
                "INSERT INTO workspace_members (workspace_id,user_id,role) "
                "VALUES (:workspace,:user,'owner')"
            ),
            {"workspace": workspace, "user": user},
        )
        conversation = await connection.scalar(
            text(
                "INSERT INTO conversations (workspace_id,created_by,title,model) "
                "VALUES (:workspace,:user,'기존 대화','old-model') RETURNING id"
            ),
            {"workspace": workspace, "user": user},
        )
        await connection.execute(
            text(
                "INSERT INTO messages "
                "(workspace_id,conversation_id,sequence,role,content,created_by) "
                "VALUES (:workspace,:conversation,1,'user','보존할 원문',:user)"
            ),
            {"workspace": workspace, "conversation": conversation, "user": user},
        )
        original = {
            table: [
                dict(row)
                for row in (
                    await connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                ).mappings()
            ]
            for table in ("users", "workspaces", "workspace_members", "conversations", "messages")
        }
    await run_alembic(postgres, "upgrade", "head")
    await database.dispose()
    async with database.session() as session:
        for table, expected in original.items():
            actual = [
                dict(row)
                for row in (
                    await session.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                ).mappings()
            ]
            if table == "users":
                assert [row.pop("platform_role") for row in actual] == ["member", "member"]
            assert actual == expected
        assert await session.scalar(select(func.count()).select_from(TokenBudget)) == 0
    await run_alembic(postgres, "check")
    await run_alembic(postgres, "downgrade", "0002_core_chat_schema")
    await database.dispose()
    assert await version_rows(database) == ["0002_core_chat_schema"]
    async with database.session() as session:
        assert await session.scalar(text("SELECT content FROM messages")) == "보존할 원문"
    await run_alembic(postgres, "upgrade", "head")
    await run_alembic(postgres, "check")


async def test_usage_basis_migration_preserves_legacy_accounting(
    postgres: IsolatedPostgres, database: Database
) -> None:
    await run_alembic(postgres, "upgrade", "0006_monthly_allowances")
    async with database.engine.begin() as connection:
        user_id = await connection.scalar(
            text(
                "INSERT INTO users (email,display_name) "
                "VALUES ('legacy-usage@example.com','기존 사용량') RETURNING id"
            )
        )
        budget_id = await connection.scalar(
            text(
                "INSERT INTO token_budgets "
                "(user_id,grant_key,grant_fingerprint,starts_at,ends_at,token_limit,"
                "used_tokens,reserved_tokens) "
                "VALUES (:user,'legacy-grant',:fingerprint,:starts,:ends,100,12,40) RETURNING id"
            ),
            {
                "user": user_id,
                "fingerprint": "a" * 64,
                "starts": NOW - timedelta(days=1),
                "ends": NOW + timedelta(days=1),
            },
        )
        for status, reserved_tokens, input_tokens, output_tokens in (
            ("reserved", 40, None, None),
            ("settled", 50, 7, 5),
            ("released", 30, 0, 0),
        ):
            await connection.execute(
                text(
                    "INSERT INTO token_reservations "
                    "(user_id,budget_id,request_key,quota_exempt,reserved_tokens,"
                    "input_tokens,output_tokens,status,completed_at) "
                    "VALUES (:user,:budget,:status,false,:reserved,:input,:output,:status,:ended)"
                ),
                {
                    "user": user_id,
                    "budget": budget_id,
                    "status": status,
                    "reserved": reserved_tokens,
                    "input": input_tokens,
                    "output": output_tokens,
                    "ended": None if status == "reserved" else NOW,
                },
            )
        original = {
            table: [
                dict(row)
                for row in (
                    await connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                ).mappings()
            ]
            for table in ("token_budgets", "token_reservations")
        }
    await database.dispose()
    await run_alembic(postgres, "upgrade", "head")
    expected_bases = {"reserved": None, "settled": "provider", "released": "waived"}
    async with database.engine.connect() as connection:
        for table, expected in original.items():
            actual = [
                dict(row)
                for row in (
                    await connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                ).mappings()
            ]
            if table == "token_reservations":
                for row in actual:
                    assert row.pop("usage_basis") == expected_bases[row["status"]]
                    assert row.pop("charge_mode") == "reserved"
            assert actual == expected
    await run_alembic(postgres, "check")
    await database.dispose()
    await run_alembic(postgres, "downgrade", "0006_monthly_allowances")
    async with database.engine.connect() as connection:
        for table, expected in original.items():
            actual = [
                dict(row)
                for row in (
                    await connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                ).mappings()
            ]
            assert actual == expected
    await run_alembic(postgres, "upgrade", "head")
    await run_alembic(postgres, "check")


async def test_system_has_unlimited_quota_but_actual_usage_is_recorded(
    schema_database: Database, actors: Actors
) -> None:
    current = await balance(schema_database, actors.system)
    assert current.unlimited is True
    assert current.remaining_tokens is None
    assert current.budget_id is None
    reservation = await reserve(schema_database, actors.system, "system-request", 10**12)
    assert reservation.quota_exempt is True
    assert reservation.budget_id is None
    async with schema_database.session() as session:
        settled = await TokenQuotaService(session, actors.system).settle(
            request_key="system-request", input_tokens=123_456, output_tokens=987_654
        )
        assert settled.id == reservation.id
        await session.commit()
    current = await balance(schema_database, actors.system)
    assert current.used_tokens == 1_111_110
    assert current.reserved_tokens == 0
    assert current.unlimited is True
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(TokenBudget)) == 0
        saved = await session.get(TokenReservation, reservation.id)
        assert saved.usage_basis == "provider"
        assert (saved.input_tokens, saved.output_tokens, saved.status) == (
            123_456,
            987_654,
            "settled",
        )


async def test_workspace_owner_is_member_without_quota_and_cannot_manage_plans(
    schema_database: Database, actors: Actors
) -> None:
    async with schema_database.session() as session:
        assert (await session.get(User, actors.member)).platform_role == "member"
        assert (
            await session.scalar(
                select(WorkspaceMember.role).where(
                    WorkspaceMember.workspace_id == actors.workspace,
                    WorkspaceMember.user_id == actors.member,
                )
            )
            == "owner"
        )
        service = TokenQuotaService(session, actors.member)
        with pytest.raises(QuotaExceeded):
            await service.reserve(request_key="no-budget", reserved_tokens=1)
        with pytest.raises(AccessDenied):
            await service.create_plan(code="unauthorized", name="금지", token_limit=100)
        with pytest.raises(AccessDenied):
            await service.grant_budget(
                user_id=actors.member,
                grant_key="unauthorized",
                starts_at=NOW,
                ends_at=NOW + timedelta(days=1),
                token_limit=100,
            )
    current = await balance(schema_database, actors.member)
    assert current.unlimited is False
    assert current.remaining_tokens == 0


async def test_disabled_system_cannot_manage_plans_or_budgets(
    schema_database: Database, actors: Actors
) -> None:
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(User).where(User.id == actors.system).values(status="disabled")
        )
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.system)
        with pytest.raises(AccessDenied):
            await service.create_plan(code="disabled", name="금지", token_limit=100)
        with pytest.raises(AccessDenied):
            await service.grant_budget(
                user_id=actors.member,
                grant_key="disabled",
                starts_at=NOW,
                ends_at=NOW + timedelta(days=1),
                token_limit=100,
            )


async def test_multiple_workspaces_share_one_user_budget(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors, limit=100)
    async with schema_database.session() as session:
        second = Workspace(name="두 번째 공간", created_by=actors.member)
        session.add(second)
        await session.flush()
        session.add(WorkspaceMember(workspace_id=second.id, user_id=actors.member, role="owner"))
        await session.flush()
        for workspace_id in (actors.workspace, second.id):
            await Repository(session, actors.member).create_conversation(workspace_id, model="test")
        await session.commit()
    await reserve(schema_database, actors.member, f"workspace:{actors.workspace}", 60)
    with pytest.raises(QuotaExceeded):
        await reserve(schema_database, actors.member, f"workspace:{second.id}", 41)
    await reserve(schema_database, actors.member, f"workspace:{second.id}", 40)
    current = await balance(schema_database, actors.member)
    assert (current.token_limit, current.reserved_tokens, current.remaining_tokens) == (100, 100, 0)


async def test_budget_periods_reject_overlap_and_exclude_exact_end(
    schema_database: Database, actors: Actors, monkeypatch
) -> None:
    await grant(schema_database, actors, limit=20, starts_at=NOW - timedelta(days=1), ends_at=NOW)
    active = await grant(
        schema_database, actors, limit=30, starts_at=NOW, ends_at=NOW + timedelta(days=1)
    )
    with pytest.raises(Conflict):
        await grant(
            schema_database,
            actors,
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(hours=1),
        )
    current = await balance(schema_database, actors.member)
    assert current.budget_id == active.id
    assert current.remaining_tokens == 30
    monkeypatch.setattr(quota_module, "utc_now", lambda: NOW + timedelta(days=1))
    assert (await balance(schema_database, actors.member)).budget_id is None
    with pytest.raises(QuotaExceeded):
        await reserve(schema_database, actors.member, "at-exact-end", 1)


async def test_plan_changes_do_not_rewrite_granted_budget_snapshot(
    schema_database: Database, actors: Actors
) -> None:
    async with schema_database.session() as session:
        plan = await TokenQuotaService(session, actors.system).create_plan(
            code="starter", name="시작 플랜", token_limit=100
        )
        await session.commit()
    original = await grant(schema_database, actors, limit=None, plan_id=plan.id, key="plan-grant")
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(UsagePlan).where(UsagePlan.id == plan.id).values(token_limit=200)
        )
    repeated = await grant(schema_database, actors, limit=None, plan_id=plan.id, key="plan-grant")
    assert repeated.id == original.id
    assert repeated.token_limit == 100
    future = await grant(
        schema_database,
        actors,
        limit=None,
        plan_id=plan.id,
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=2),
    )
    assert future.token_limit == 200
    assert (await balance(schema_database, actors.member)).token_limit == 100


async def test_settlement_charges_input_and_output_and_refunds_unused_reservation(
    schema_database: Database, actors: Actors
) -> None:
    budget = await grant(schema_database, actors)
    saved = await reserve(schema_database, actors.member, "settle", 80)
    assert (await balance(schema_database, actors.member)).remaining_tokens == 20
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        result = await service.settle(request_key="settle", input_tokens=20, output_tokens=10)
        repeated = await service.settle(request_key="settle", input_tokens=20, output_tokens=10)
        assert result.id == repeated.id == saved.id
        assert result.usage_basis == "provider"
        with pytest.raises(Conflict):
            await service.settle(request_key="settle", input_tokens=21, output_tokens=10)
        with pytest.raises(Conflict):
            await service.release(request_key="settle")
        await session.commit()
    current = await balance(schema_database, actors.member)
    assert current.budget_id == budget.id
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (30, 0, 70)


async def test_failed_request_release_is_idempotent_and_refunds_all_tokens(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    original = await reserve(schema_database, actors.member, "failed", 60)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        first = await service.release(request_key="failed")
        second = await service.release(request_key="failed")
        assert first.id == second.id == original.id
        assert (first.input_tokens, first.output_tokens, first.status) == (0, 0, "released")
        assert first.usage_basis == "waived"
        with pytest.raises(Conflict):
            await service.settle(request_key="failed", input_tokens=0, output_tokens=0)
        await session.commit()
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 0, 100)


@pytest.mark.parametrize(
    ("usage_basis", "input_tokens", "output_tokens"),
    [("received", 20, 3), ("waived", 0, 0)],
)
@pytest.mark.parametrize("actor_name", ["member", "system"])
async def test_cancelled_settlement_preserves_charge_basis_and_refunds_unused_tokens(
    schema_database: Database,
    actors: Actors,
    usage_basis: str,
    input_tokens: int,
    output_tokens: int,
    actor_name: str,
) -> None:
    actor_id = getattr(actors, actor_name)
    if actor_name == "member":
        await grant(schema_database, actors)
    original = await reserve(schema_database, actor_id, "cancelled", 80)
    assert original.usage_basis is None
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actor_id)
        arguments = {
            "request_key": "cancelled",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usage_basis": usage_basis,
        }
        result = await service.settle(**arguments)
        repeated = await service.settle(**arguments)
        assert result.id == repeated.id == original.id
        assert (result.status, result.usage_basis) == ("settled", usage_basis)
        with pytest.raises(Conflict):
            await service.settle(**{**arguments, "usage_basis": "provider"})
        await session.commit()
    current = await balance(schema_database, actor_id)
    assert current.used_tokens == input_tokens + output_tokens
    assert current.reserved_tokens == 0
    assert current.remaining_tokens == (
        100 - input_tokens - output_tokens if actor_name == "member" else None
    )
    async with schema_database.session() as session:
        saved = await session.get(TokenReservation, original.id)
        assert (saved.input_tokens, saved.output_tokens, saved.usage_basis) == (
            input_tokens,
            output_tokens,
            usage_basis,
        )


async def test_invalid_usage_basis_and_nonzero_waiver_preserve_pending_reservation(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    original = await reserve(schema_database, actors.member, "invalid-basis", 60)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        for usage_basis in ("estimated", "", None, True, []):
            with pytest.raises(InvalidInput):
                await service.settle(
                    request_key="invalid-basis",
                    input_tokens=0,
                    output_tokens=0,
                    usage_basis=usage_basis,
                )
        for input_tokens, output_tokens in ((1, 0), (0, 1)):
            with pytest.raises(InvalidInput):
                await service.settle(
                    request_key="invalid-basis",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_basis="waived",
                )
        await session.commit()
    assert (await balance(schema_database, actors.member)).reserved_tokens == 60
    async with schema_database.session() as session:
        saved = await session.get(TokenReservation, original.id)
        assert (saved.status, saved.usage_basis, saved.input_tokens, saved.completed_at) == (
            "reserved",
            None,
            None,
            None,
        )


async def test_reservation_key_retries_do_not_allocate_twice(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    original = await reserve(schema_database, actors.member, "same-key", 60)
    assert (await reserve(schema_database, actors.member, "same-key", 60)).id == original.id
    with pytest.raises(Conflict):
        await reserve(schema_database, actors.member, "same-key", 61)
    assert (await balance(schema_database, actors.member)).reserved_tokens == 60
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).release(request_key="same-key")
        await session.commit()
    repeated = await reserve(schema_database, actors.member, "same-key", 60)
    assert repeated.id == original.id
    assert repeated.status == "released"
    assert (await balance(schema_database, actors.member)).reserved_tokens == 0


async def test_grant_key_retries_preserve_budget_and_conflicting_payload_is_rejected(
    schema_database: Database, actors: Actors
) -> None:
    original = await grant(schema_database, actors, key="payment-event-1")
    repeated = await grant(schema_database, actors, key="payment-event-1")
    assert original.id == repeated.id
    with pytest.raises(Conflict):
        await grant(schema_database, actors, key="payment-event-1", limit=101)
    with pytest.raises(Conflict):
        await grant(schema_database, actors, key="payment-event-1", user_id=actors.other)
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(TokenBudget)) == 1


async def test_expired_reservation_settles_original_budget_not_current_period(
    schema_database: Database, actors: Actors, monkeypatch
) -> None:
    old_budget = await grant(schema_database, actors, ends_at=NOW + timedelta(hours=1))
    await reserve(schema_database, actors.member, "old-period", 80)
    next_budget = await grant(
        schema_database,
        actors,
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(days=2),
        limit=200,
    )
    monkeypatch.setattr(quota_module, "utc_now", lambda: NOW + timedelta(hours=1))
    async with schema_database.session() as session:
        reservation = await TokenQuotaService(session, actors.member).settle(
            request_key="old-period", input_tokens=20, output_tokens=10
        )
        assert reservation.budget_id == old_budget.id
        await session.commit()
    current = await balance(schema_database, actors.member)
    assert current.budget_id == next_budget.id
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 0, 200)
    async with schema_database.session() as session:
        stored = await session.get(TokenBudget, old_budget.id)
        assert (stored.used_tokens, stored.reserved_tokens) == (30, 0)


async def test_reservations_and_request_keys_are_isolated_by_user(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    first = await reserve(schema_database, actors.member, "shared-key", 30)
    async with schema_database.session() as session:
        other = TokenQuotaService(session, actors.other)
        with pytest.raises(AccessDenied):
            await other.settle(request_key="shared-key", input_tokens=1, output_tokens=1)
        with pytest.raises(AccessDenied):
            await other.release(request_key="shared-key")
    await grant(schema_database, actors, user_id=actors.other)
    second = await reserve(schema_database, actors.other, "shared-key", 20)
    assert first.id != second.id
    assert (await balance(schema_database, actors.member)).reserved_tokens == 30
    assert (await balance(schema_database, actors.other)).reserved_tokens == 20


@pytest.mark.parametrize("completion", ["settle", "release"])
async def test_disabled_user_cannot_reserve_but_can_finish_existing_accounting(
    schema_database: Database, actors: Actors, completion: str
) -> None:
    budget = await grant(schema_database, actors)
    await reserve(schema_database, actors.member, "outstanding", 60)
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(User).where(User.id == actors.member).values(status="disabled")
        )
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        with pytest.raises(AccessDenied):
            await service.reserve(request_key="new-disabled", reserved_tokens=1)
        with pytest.raises(AccessDenied):
            await service.get_balance()
        if completion == "settle":
            await service.settle(request_key="outstanding", input_tokens=10, output_tokens=5)
        else:
            await service.release(request_key="outstanding")
        await session.commit()
    async with schema_database.session() as session:
        saved = await session.get(TokenBudget, budget.id)
        assert saved.used_tokens == (15 if completion == "settle" else 0)
        assert saved.reserved_tokens == 0


async def test_invalid_token_counts_and_actual_over_reservation_leave_counters_unchanged(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        for invalid in (-1, 0, True, False, 1.5, 2**63):
            with pytest.raises(InvalidInput):
                await service.reserve(request_key="invalid", reserved_tokens=invalid)
        valid = await service.reserve(request_key="valid", reserved_tokens=60)
        for input_tokens, output_tokens in [
            (-1, 0),
            (True, 0),
            (0, False),
            (2**63, 0),
            (50, 11),
            (2**63 - 1, 1),
        ]:
            with pytest.raises(InvalidInput):
                await service.settle(
                    request_key="valid", input_tokens=input_tokens, output_tokens=output_tokens
                )
        assert valid.status == "reserved"
        await session.commit()
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 60, 40)
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 1


async def test_transaction_rollback_restores_reservation_and_settlement_counters(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).reserve(
            request_key="rollback", reserved_tokens=60
        )
    assert (await balance(schema_database, actors.member)).reserved_tokens == 0
    original = await reserve(schema_database, actors.member, "rollback", 60)
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).settle(
            request_key="rollback", input_tokens=10, output_tokens=10
        )
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens) == (0, 60)
    async with schema_database.session() as session:
        assert (await session.get(TokenReservation, original.id)).status == "reserved"
        await TokenQuotaService(session, actors.member).release(request_key="rollback")
    assert (await balance(schema_database, actors.member)).reserved_tokens == 60


async def test_concurrent_reservations_cannot_exceed_budget(
    schema_database: Database, postgres: IsolatedPostgres, actors: Actors
) -> None:
    await grant(schema_database, actors)
    second_database = Database(database_settings(postgres))
    first_reserved = asyncio.Event()
    release_first = asyncio.Event()

    async def attempt(database, key, hold):
        async with database.session() as session:
            result = await TokenQuotaService(session, actors.member).reserve(
                request_key=key, reserved_tokens=60
            )
            if hold:
                first_reserved.set()
                await release_first.wait()
            await session.commit()
            return result.id

    first_task = asyncio.create_task(attempt(schema_database, "concurrent-1", True))
    second_task = None
    blocked = False
    try:
        await asyncio.wait_for(first_reserved.wait(), timeout=5)
        second_task = asyncio.create_task(attempt(second_database, "concurrent-2", False))
        try:
            async with asyncio.timeout(5):
                while not second_task.done():
                    blocked = bool(
                        await postgres.admin.fetchval(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=$1 AND wait_event_type='Lock')",
                            postgres.name,
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
        finally:
            release_first.set()
        results = await asyncio.wait_for(
            asyncio.gather(first_task, second_task, return_exceptions=True), 5
        )
        assert blocked
        assert isinstance(results[0], UUID)
        assert isinstance(results[1], QuotaExceeded)
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task), return_exceptions=True
        )
        await second_database.dispose()
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 60, 40)
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 1


async def test_database_constraints_reject_cross_user_budget_and_invalid_accounting(
    schema_database: Database, actors: Actors
) -> None:
    budget = await grant(schema_database, actors)
    other_budget = await grant(schema_database, actors, user_id=actors.other)
    reservation = await reserve(schema_database, actors.member, "db-constraints", 60)
    parameters = {
        "user": actors.member,
        "budget": budget.id,
        "other_budget": other_budget.id,
        "reservation": reservation.id,
    }
    invalid_statements = [
        "UPDATE users SET platform_role='owner' WHERE id=:user",
        "UPDATE token_budgets SET token_limit=-1 WHERE id=:budget",
        "UPDATE token_budgets SET used_tokens=-1 WHERE id=:budget",
        "UPDATE token_budgets SET reserved_tokens=-1 WHERE id=:budget",
        "UPDATE token_budgets SET used_tokens=50 WHERE id=:budget",
        "UPDATE token_budgets SET ends_at=starts_at WHERE id=:budget",
        "UPDATE token_budgets SET grant_fingerprint='not-a-hash' WHERE id=:budget",
        "UPDATE token_budgets SET token_limit=9223372036854775807,"
        "used_tokens=9223372036854775807,reserved_tokens=9223372036854775807 WHERE id=:budget",
        "UPDATE token_reservations SET budget_id=:other_budget WHERE id=:reservation",
        "UPDATE token_reservations SET budget_id=NULL WHERE id=:reservation",
        "UPDATE token_reservations SET quota_exempt=true WHERE id=:reservation",
        "UPDATE token_reservations SET reserved_tokens=0 WHERE id=:reservation",
        "UPDATE token_reservations SET input_tokens=1 WHERE id=:reservation",
        "UPDATE token_reservations SET status='settled' WHERE id=:reservation",
        "UPDATE token_reservations SET status='released',completed_at=now() WHERE id=:reservation",
        "UPDATE token_reservations SET status='settled',usage_basis='provider',completed_at=now(),"
        "input_tokens=60,output_tokens=1 WHERE id=:reservation",
        "UPDATE token_reservations SET status='settled',usage_basis='provider',completed_at=now(),"
        "input_tokens=9223372036854775807,output_tokens=9223372036854775807 WHERE id=:reservation",
    ]
    async with schema_database.engine.begin() as connection:
        for statement in invalid_statements:
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text(statement), parameters)


async def test_database_constraints_require_a_valid_usage_basis_for_each_state(
    schema_database: Database, actors: Actors
) -> None:
    reservation = await reserve(schema_database, actors.system, "basis-constraints", 60)
    invalid_changes = [
        "usage_basis='provider'",
        "usage_basis='waived'",
        "status='settled',input_tokens=1,output_tokens=1,completed_at=now(),usage_basis=NULL",
        "status='settled',input_tokens=1,output_tokens=1,completed_at=now(),usage_basis='estimated'",
        "status='settled',input_tokens=1,output_tokens=0,completed_at=now(),usage_basis='waived'",
        "status='settled',input_tokens=0,output_tokens=1,completed_at=now(),usage_basis='waived'",
        "status='released',input_tokens=0,output_tokens=0,completed_at=now(),usage_basis=NULL",
        "status='released',input_tokens=0,output_tokens=0,completed_at=now(),usage_basis='provider'",
        "status='released',input_tokens=0,output_tokens=0,completed_at=now(),usage_basis='received'",
    ]
    async with schema_database.engine.begin() as connection:
        for changes in invalid_changes:
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(f"UPDATE token_reservations SET {changes} WHERE id=:reservation"),
                        {"reservation": reservation.id},
                    )


@pytest.mark.parametrize("actor_name", ["member", "system"])
@pytest.mark.parametrize(
    ("usage_basis", "input_tokens", "output_tokens"),
    [("provider", 20, 10), ("received", 20, 3), ("waived", 0, 0)],
)
async def test_deferred_request_keeps_balance_until_final_usage_is_settled_once(
    schema_database: Database,
    actors: Actors,
    actor_name: str,
    usage_basis: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    actor_id = getattr(actors, actor_name)
    if actor_name == "member":
        await grant(schema_database, actors)
    before = await balance(schema_database, actor_id)
    request = await begin_deferred(schema_database, actor_id, "after-answer", 80)
    assert request.charge_mode == "deferred"
    assert request.reserved_tokens == 80
    assert await balance(schema_database, actor_id) == before
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actor_id)
        arguments = {
            "request_key": "after-answer",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usage_basis": usage_basis,
        }
        first = await service.settle(**arguments)
        repeated = await service.settle(**arguments)
        assert first.id == repeated.id == request.id
        with pytest.raises(Conflict):
            await service.settle(
                **{**arguments, "output_tokens": output_tokens + 1, "usage_basis": "provider"}
            )
        await session.commit()
    current = await balance(schema_database, actor_id)
    assert current.used_tokens == input_tokens + output_tokens
    assert current.reserved_tokens == 0
    assert current.remaining_tokens == (
        100 - input_tokens - output_tokens if actor_name == "member" else None
    )
    assert (await begin_deferred(schema_database, actor_id, "after-answer", 80)).id == request.id
    # 정산 후 새 요청은 허용하되 다음 답변이 끝나기 전 잔여량은 그대로 유지한다.
    await begin_deferred(schema_database, actor_id, "next-answer", 60)
    assert await balance(schema_database, actor_id) == current


@pytest.mark.parametrize("actor_name", ["member", "system"])
async def test_pending_deferred_request_blocks_additional_execution_and_mode_changes(
    schema_database: Database, actors: Actors, actor_name: str
) -> None:
    actor_id = getattr(actors, actor_name)
    if actor_name == "member":
        await grant(schema_database, actors)
    original = await begin_deferred(schema_database, actor_id, "pending", 60)
    assert (await begin_deferred(schema_database, actor_id, "pending", 60)).id == original.id
    for key, amount in (("pending", 61), ("new", 1)):
        with pytest.raises(Conflict):
            await begin_deferred(schema_database, actor_id, key, amount)
    for key in ("pending", "legacy-bypass"):
        with pytest.raises(Conflict):
            await reserve(schema_database, actor_id, key, 60)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actor_id)
        released = await service.release(request_key="pending")
        assert (await service.release(request_key="pending")).id == released.id
        assert (released.usage_basis, released.input_tokens, released.output_tokens) == (
            "waived",
            0,
            0,
        )
        await session.commit()
    assert (await balance(schema_database, actor_id)).used_tokens == 0
    assert (await balance(schema_database, actor_id)).reserved_tokens == 0
    legacy = await reserve(schema_database, actor_id, "legacy", 20)
    assert legacy.charge_mode == "reserved"
    with pytest.raises(Conflict):
        await begin_deferred(schema_database, actor_id, "legacy", 20)


async def test_deferred_authorization_respects_legacy_reservations_and_preserves_their_counters(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    await reserve(schema_database, actors.member, "legacy", 40)
    with pytest.raises(QuotaExceeded):
        await begin_deferred(schema_database, actors.member, "too-large", 61)
    await begin_deferred(schema_database, actors.member, "new", 60)
    assert (await balance(schema_database, actors.member)).remaining_tokens == 60
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        await service.settle(request_key="new", input_tokens=10, output_tokens=20)
        await session.commit()
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (30, 40, 30)
    await begin_deferred(schema_database, actors.member, "unused", 30)
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).release(request_key="unused")
        await session.commit()
    assert await balance(schema_database, actors.member) == current


async def test_deferred_authorization_and_settlement_rollback_leave_balances_unchanged(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).begin_deferred(
            request_key="rollback", authorized_tokens=60
        )
    async with schema_database.session() as session:
        assert await session.scalar(select(func.count()).select_from(TokenReservation)) == 0
    request = await begin_deferred(schema_database, actors.member, "rollback", 60)
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).settle(
            request_key="rollback", input_tokens=10, output_tokens=20
        )
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 0, 100)
    async with schema_database.session() as session:
        assert (await session.get(TokenReservation, request.id)).status == "reserved"
        await TokenQuotaService(session, actors.member).release(request_key="rollback")
    with pytest.raises(Conflict):
        await begin_deferred(schema_database, actors.member, "blocked-until-commit", 1)


@pytest.mark.parametrize("completion", ["settle", "release"])
async def test_deferred_request_finishes_original_period_after_expiry_and_account_disable(
    schema_database: Database, actors: Actors, monkeypatch, completion: str
) -> None:
    old_budget = await grant(schema_database, actors, ends_at=NOW + timedelta(hours=1))
    await begin_deferred(schema_database, actors.member, "old-period", 80)
    next_budget = await grant(
        schema_database,
        actors,
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(days=2),
        limit=200,
    )
    monkeypatch.setattr(quota_module, "utc_now", lambda: NOW + timedelta(hours=1))
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(User).where(User.id == actors.member).values(status="disabled")
        )
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        with pytest.raises(AccessDenied):
            await service.begin_deferred(request_key="disabled", authorized_tokens=1)
        if completion == "settle":
            await service.settle(request_key="old-period", input_tokens=20, output_tokens=5)
        else:
            await service.release(request_key="old-period")
        await session.commit()
    async with schema_database.session() as session:
        old = await session.get(TokenBudget, old_budget.id)
        current = await session.get(TokenBudget, next_budget.id)
        assert (old.used_tokens, old.reserved_tokens) == (25 if completion == "settle" else 0, 0)
        assert (current.used_tokens, current.reserved_tokens) == (0, 0)


async def test_deferred_invalid_counts_and_changed_budget_cannot_overcharge(
    schema_database: Database, actors: Actors
) -> None:
    budget = await grant(schema_database, actors)
    async with schema_database.session() as session:
        service = TokenQuotaService(session, actors.member)
        for invalid in (-1, 0, True, False, 1.5, 2**63):
            with pytest.raises(InvalidInput):
                await service.begin_deferred(request_key="invalid", authorized_tokens=invalid)
    request = await begin_deferred(schema_database, actors.member, "valid", 60)
    async with schema_database.session() as session:
        with pytest.raises(InvalidInput):
            await TokenQuotaService(session, actors.member).settle(
                request_key="valid", input_tokens=50, output_tokens=11
            )
    async with schema_database.engine.begin() as connection:
        await connection.execute(
            update(TokenBudget).where(TokenBudget.id == budget.id).values(used_tokens=90)
        )
    async with schema_database.session() as session:
        with pytest.raises(Conflict):
            await TokenQuotaService(session, actors.member).settle(
                request_key="valid", input_tokens=10, output_tokens=10
            )
    async with schema_database.session() as session:
        assert (await session.get(TokenReservation, request.id)).status == "reserved"
        assert (await session.get(TokenBudget, budget.id)).used_tokens == 90


async def test_concurrent_deferred_requests_allow_only_one_without_reserving_balance(
    schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    results = await asyncio.gather(
        *(begin_deferred(schema_database, actors.member, key, 60) for key in ("first", "second")),
        return_exceptions=True,
    )
    assert sum(isinstance(result, TokenReservation) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 1
    current = await balance(schema_database, actors.member)
    assert (current.used_tokens, current.reserved_tokens, current.remaining_tokens) == (0, 0, 100)


async def test_database_rejects_invalid_charge_mode_and_duplicate_pending_deferred_user(
    schema_database: Database, actors: Actors
) -> None:
    request = await begin_deferred(schema_database, actors.system, "first", 60)
    async with schema_database.engine.begin() as connection:
        for value in ("unknown", None):
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text("UPDATE token_reservations SET charge_mode=:value WHERE id=:id"),
                        {"value": value, "id": request.id},
                    )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    text(
                        "INSERT INTO token_reservations "
                        "(user_id,request_key,quota_exempt,reserved_tokens,charge_mode) "
                        "VALUES (:user,'duplicate',true,60,'deferred')"
                    ),
                    {"user": actors.system},
                )


async def test_downgrade_blocks_pending_deferred_then_preserves_completed_usage(
    postgres: IsolatedPostgres, schema_database: Database, actors: Actors
) -> None:
    await grant(schema_database, actors)
    request = await begin_deferred(schema_database, actors.member, "migration", 60)
    await schema_database.dispose()
    await run_alembic(postgres, "downgrade", "0007_cancellation_usage", success=False)
    assert await version_rows(schema_database) == ["0012_network_search"]
    async with schema_database.session() as session:
        await TokenQuotaService(session, actors.member).settle(
            request_key="migration", input_tokens=10, output_tokens=5, usage_basis="received"
        )
        await session.commit()
    await schema_database.dispose()
    await run_alembic(postgres, "downgrade", "0007_cancellation_usage")
    async with schema_database.engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT input_tokens,output_tokens,usage_basis,status "
                    "FROM token_reservations WHERE id=:id"
                ),
                {"id": request.id},
            )
        ).one()
        assert tuple(row) == (10, 5, "received", "settled")
        assert await connection.scalar(text("SELECT used_tokens FROM token_budgets")) == 15
    await run_alembic(postgres, "upgrade", "head")
    await run_alembic(postgres, "check")
