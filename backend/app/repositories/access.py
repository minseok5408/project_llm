"""활성 계정·작업 공간 소속·대화 수정 권한과 잠금 순서를 공유한다."""

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Conversation, User, Workspace, WorkspaceMember
from backend.app.repositories.errors import AccessDenied
from backend.app.repositories.validation import identifier


class RepositoryAccess:
    """각 저장소가 같은 세션과 사용자로 권한을 확인하는 공개 계약."""

    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = identifier(actor_id)

    def membership_query(self, workspace_id: UUID):
        return (
            select(WorkspaceMember)
            .join(User, User.id == WorkspaceMember.user_id)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(
                WorkspaceMember.user_id == self.actor_id,
                WorkspaceMember.workspace_id == workspace_id,
                User.status == "active",
                Workspace.status == "active",
            )
        )

    def exists(self, workspace_id: UUID):
        return exists(self.membership_query(workspace_id))

    async def require_membership(
        self, workspace_id: UUID, *, lock: bool = False
    ) -> WorkspaceMember:
        statement = self.membership_query(workspace_id).execution_options(populate_existing=True)
        if lock:
            # 쓰기 트랜잭션 중 계정 비활성화·소속 철회·역할 변경이 먼저 확정되지 않게 한다.
            statement = statement.with_for_update(read=True, of=(User, Workspace, WorkspaceMember))
        membership = await self.session.scalar(statement)
        if membership is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return membership

    def conversation_query(self, workspace_id: UUID, conversation_id: UUID):
        return (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self.exists(workspace_id),
            )
            .execution_options(populate_existing=True)
        )

    async def lock_editable_conversation(
        self, workspace_id: UUID, conversation_id: UUID
    ) -> Conversation:
        # 생성 admission과 같은 사용자 → 소속 → 대화 순서로 잠가 잠금 승격 교착을 막는다.
        await self.session.flush()
        user_id = await self.session.scalar(
            select(User.id)
            .where(User.id == self.actor_id, User.status == "active")
            .with_for_update()
        )
        if user_id is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        membership = await self.require_membership(workspace_id, lock=True)
        conversation = await self.session.scalar(
            self.conversation_query(workspace_id, conversation_id).with_for_update(of=Conversation)
        )
        if conversation is None or (
            conversation.created_by != self.actor_id and membership.role not in ("owner", "admin")
        ):
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return conversation
