"""시스템 계정 초기화의 명시성·멱등성과 기존 계정 보호를 검사한다."""

import pytest
from sqlalchemy import func, select

from backend.app.models import AuthSession, User, Workspace, WorkspaceMember
from backend.app.repositories import AccessDenied, create_user_with_workspace
from backend.app.services.system_accounts import bootstrap_system_user

pytestmark = pytest.mark.postgres


async def test_system_bootstrap_is_explicit_and_idempotent(schema_database):
    async with schema_database.session() as session:
        ordinary = await create_user_with_workspace(
            session, email="system@example.com", display_name="system"
        )
        assert ordinary.user.platform_role == "member"
        user_id, workspace_id = ordinary.user.id, ordinary.workspace.id
        await session.commit()

    async with schema_database.session() as session:
        first = await bootstrap_system_user(session, email=" System@Example.com ")
        second = await bootstrap_system_user(session, email="system@example.com")
        assert first.id == second.id == user_id
        assert second.platform_role == "system"
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Workspace)) == 1
        assert await session.scalar(select(WorkspaceMember.workspace_id)) == workspace_id
        await session.commit()


async def test_system_bootstrap_creates_workspace_and_respects_disabled_account(schema_database):
    async with schema_database.session() as session:
        user = await bootstrap_system_user(session, email="operator@example.com")
        assert user.platform_role == "system"
        assert user.display_name == "system"
        assert await session.scalar(select(func.count()).select_from(WorkspaceMember)) == 1
        user.status = "disabled"
        await session.commit()

    async with schema_database.session() as session:
        with pytest.raises(AccessDenied):
            await bootstrap_system_user(session, email="operator@example.com")
        assert await session.scalar(select(User.status)) == "disabled"


async def test_promotion_revokes_old_sessions_but_repeat_bootstrap_keeps_new_ones(schema_database):
    async with schema_database.session() as session:
        created = await create_user_with_workspace(
            session, email="promotion@example.com", display_name="승격 대상"
        )
        user_id = created.user.id
        session.add(AuthSession(user_id=user_id, token_hash="a" * 64))
        await session.commit()

    async with schema_database.session() as session:
        await bootstrap_system_user(session, email="promotion@example.com")
        await session.commit()

    async with schema_database.session() as session:
        assert await session.scalar(select(AuthSession.revoked_at)) is not None
        session.add(AuthSession(user_id=user_id, token_hash="b" * 64))
        await session.commit()

    async with schema_database.session() as session:
        await bootstrap_system_user(session, email="promotion@example.com")
        await session.commit()

    async with schema_database.session() as session:
        current = await session.scalar(
            select(AuthSession).where(AuthSession.token_hash == "b" * 64)
        )
        assert current.revoked_at is None
