"""사용자·기본 작업 공간 생성과 접근 가능한 계정·소속 조회."""

import re
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import User, Workspace, WorkspaceMember
from backend.app.repositories.access import RepositoryAccess
from backend.app.repositories.errors import AccessDenied, Conflict, InvalidInput
from backend.app.repositories.operations import logged
from backend.app.repositories.types import UserWorkspace
from backend.app.repositories.validation import identifier, required_text


@logged
async def create_user_with_workspace(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
    workspace_name: str | None = None,
) -> UserWorkspace:
    """내부 계정 생성용 함수이며 사용자·기본 소속을 만들되 인증이나 커밋은 하지 않는다."""
    normalized_email = required_text(email, 320, "이메일").lower()
    if (
        len(normalized_email) > 320
        or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized_email) is None
    ):
        raise InvalidInput("이메일 형식이 올바르지 않습니다.")
    normalized_name = required_text(display_name, 200, "표시 이름")
    normalized_workspace = required_text(
        workspace_name if workspace_name is not None else "기본 워크스페이스",
        200,
        "워크스페이스 이름",
    )
    if await session.scalar(select(User.id).where(User.email == normalized_email)) is not None:
        raise Conflict("이미 사용 중인 이메일입니다.")

    user = User(id=uuid4(), email=normalized_email, display_name=normalized_name)
    session.add(user)
    await session.flush()
    workspace = Workspace(id=uuid4(), name=normalized_workspace, created_by=user.id)
    session.add(workspace)
    await session.flush()
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    session.add(membership)
    await session.flush()
    return UserWorkspace(user=user, workspace=workspace, membership=membership)


class AccountRepository:
    """계정과 작업 공간의 조회·생성을 담당하며 커밋은 호출자에게 맡긴다."""

    def __init__(self, access: RepositoryAccess):
        self.access = access
        self.session, self.actor_id = access.session, access.actor_id

    @logged
    async def get_user(self) -> User:
        user = await self.session.scalar(
            select(User)
            .where(User.id == self.actor_id, User.status == "active")
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return user

    @logged
    async def list_workspaces(self) -> list[Workspace]:
        await self.get_user()
        statement = (
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(User.id == self.actor_id, User.status == "active", Workspace.status == "active")
            .order_by(Workspace.created_at, Workspace.id)
            .execution_options(populate_existing=True)
        )
        return list((await self.session.scalars(statement)).all())

    @logged
    async def list_workspaces_with_roles(self) -> list[tuple[Workspace, str]]:
        """기존 소속만 반환하며 조회를 위해 기본 작업 공간을 새로 만들지 않는다."""
        await self.get_user()
        rows = await self.session.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(User.id == self.actor_id, User.status == "active", Workspace.status == "active")
            .order_by(Workspace.created_at, Workspace.id)
        )
        return [(workspace, role) for workspace, role in rows]

    @logged
    async def get_workspace(self, workspace_id: UUID) -> Workspace:
        workspace_id = identifier(workspace_id)
        workspace = await self.session.scalar(
            select(Workspace)
            .where(Workspace.id == workspace_id, self.access.exists(workspace_id))
            .execution_options(populate_existing=True)
        )
        if workspace is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return workspace
