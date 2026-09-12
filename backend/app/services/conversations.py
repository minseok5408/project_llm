"""대화와 첨부 파일에 걸친 삭제를 하나의 트랜잭션으로 조합한다."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.files.deletion import mark_conversation_files_deleted
from backend.app.repositories.access import RepositoryAccess
from backend.app.repositories.conversations import ConversationRepository
from backend.app.repositories.operations import logged


class ConversationService:
    def __init__(self, session: AsyncSession, actor_id: UUID):
        self.session = session
        self.conversations = ConversationRepository(RepositoryAccess(session, actor_id))

    @logged
    async def soft_delete_conversation(self, workspace_id: UUID, conversation_id: UUID) -> None:
        """기존 사용자→소속→대화 잠금 순서를 지키며 원문은 보존하고 파일 검색을 차단한다."""
        conversation = await self.conversations.mark_deleted(workspace_id, conversation_id)
        await mark_conversation_files_deleted(
            self.session, conversation.id, conversation.deleted_at
        )
        await self.session.flush()
