"""기존 조회·저장 호출면을 유지하고 역할별 저장소를 같은 세션으로 조합한다."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Conversation, Message, User, Workspace, WorkspaceMember
from backend.app.repositories.access import RepositoryAccess
from backend.app.repositories.accounts import AccountRepository
from backend.app.repositories.conversations import ConversationRepository
from backend.app.repositories.messages import MessageRepository
from backend.app.repositories.types import Page


class Repository:
    """데이터 조회·저장 진입점. 여러 영역의 삭제 절차는 별도 서비스가 조정한다."""

    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.access = RepositoryAccess(session, actor_id)
        self.session, self.actor_id = self.access.session, self.access.actor_id
        self.accounts = AccountRepository(self.access)
        self.conversations = ConversationRepository(self.access)
        self.messages = MessageRepository(self.access, self.conversations)

    async def get_user(self) -> User:
        return await self.accounts.get_user()

    async def list_workspaces(self) -> list[Workspace]:
        return await self.accounts.list_workspaces()

    async def list_workspaces_with_roles(self) -> list[tuple[Workspace, str]]:
        return await self.accounts.list_workspaces_with_roles()

    async def get_workspace(self, workspace_id: UUID) -> Workspace:
        return await self.accounts.get_workspace(workspace_id)

    async def create_conversation(
        self,
        workspace_id: UUID,
        *,
        model: str,
        title: str = "새 대화",
    ) -> Conversation:
        return await self.conversations.create_conversation(workspace_id, model=model, title=title)

    async def get_conversation(self, workspace_id: UUID, conversation_id: UUID) -> Conversation:
        return await self.conversations.get_conversation(workspace_id, conversation_id)

    async def get_conversation_by_id(self, conversation_id: UUID) -> Conversation:
        return await self.conversations.get_conversation_by_id(conversation_id)

    async def active_generation_ids(self, conversation_ids: list[UUID]) -> dict[UUID, UUID]:
        return await self.conversations.active_generation_ids(conversation_ids)

    async def list_conversations(
        self,
        workspace_id: UUID,
        *,
        limit: int = 30,
        cursor: str | None = None,
        status: str = "active",
        q: str | None = None,
    ) -> Page[Conversation]:
        return await self.conversations.list_conversations(
            workspace_id, limit=limit, cursor=cursor, status=status, q=q
        )

    async def update_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        title: str | None = None,
        is_pinned: bool | None = None,
        status: str | None = None,
    ) -> Conversation:
        return await self.conversations.update_conversation(
            workspace_id, conversation_id, title=title, is_pinned=is_pinned, status=status
        )

    async def append_message(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        role: str,
        content: str,
        status: str = "completed",
        token_count: int | None = None,
        model: str | None = None,
    ) -> Message:
        return await self.messages.append_message(
            workspace_id,
            conversation_id,
            role=role,
            content=content,
            status=status,
            token_count=token_count,
            model=model,
        )

    async def list_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        limit: int = 50,
        before: int | None = None,
        after: int | None = None,
        around: UUID | None = None,
    ) -> Page[Message]:
        return await self.messages.list_messages(
            workspace_id, conversation_id, limit=limit, before=before, after=after, around=around
        )

    async def require_membership(
        self, workspace_id: UUID, *, lock: bool = False
    ) -> WorkspaceMember:
        return await self.access.require_membership(workspace_id, lock=lock)
