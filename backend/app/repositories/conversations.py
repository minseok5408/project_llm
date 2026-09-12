"""대화 조회·검색·수정과 삭제 표시를 같은 접근 권한 계약으로 처리한다."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select, tuple_

from backend.app.models import Conversation, Message, User, Workspace, WorkspaceMember
from backend.app.repositories.access import RepositoryAccess
from backend.app.repositories.errors import AccessDenied, Conflict, InvalidInput
from backend.app.repositories.operations import logged
from backend.app.repositories.pagination import decode_cursor, encode_cursor
from backend.app.repositories.types import Page
from backend.app.repositories.validation import identifier, page_limit, required_text


class ConversationRepository:
    """대화 메타데이터를 담당하며 파일 저장소와 정산 구현에는 의존하지 않는다."""

    def __init__(self, access: RepositoryAccess):
        self.access = access
        self.session, self.actor_id = access.session, access.actor_id

    @logged
    async def create_conversation(
        self,
        workspace_id: UUID,
        *,
        model: str,
        title: str = "새 대화",
    ) -> Conversation:
        workspace_id = identifier(workspace_id)
        title = required_text(title, 300, "대화 제목")
        model = required_text(model, 255, "모델")
        await self.access.require_membership(workspace_id, lock=True)
        conversation = Conversation(
            workspace_id=workspace_id,
            created_by=self.actor_id,
            title=title,
            model=model,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    @logged
    async def get_conversation(self, workspace_id: UUID, conversation_id: UUID) -> Conversation:
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        conversation = await self.session.scalar(
            self.access.conversation_query(workspace_id, conversation_id)
        )
        if conversation is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return conversation

    @logged
    async def get_conversation_by_id(self, conversation_id: UUID) -> Conversation:
        """단일 UUID도 현재 사용자의 소속 범위 안에서만 조회한다."""
        conversation_id = identifier(conversation_id)
        conversation = await self.session.scalar(
            select(Conversation)
            .join(Workspace, Workspace.id == Conversation.workspace_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
                Workspace.status == "active",
                WorkspaceMember.user_id == self.actor_id,
                User.status == "active",
            )
            .execution_options(populate_existing=True)
        )
        if conversation is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return conversation

    @logged
    async def active_generation_ids(self, conversation_ids: list[UUID]) -> dict[UUID, UUID]:
        """허용된 대화의 실행 중인 생성만 조회하고 끝난 회계 보류 작업은 제외한다."""
        from backend.app.models.generations import GenerationRun

        if not conversation_ids:
            return {}
        conversation_ids = [identifier(value) for value in conversation_ids]
        rows = await self.session.execute(
            select(GenerationRun.conversation_id, GenerationRun.id)
            .join(
                Conversation,
                (Conversation.id == GenerationRun.conversation_id)
                & (Conversation.workspace_id == GenerationRun.workspace_id),
            )
            .join(Workspace, Workspace.id == Conversation.workspace_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(
                Conversation.id.in_(conversation_ids),
                Conversation.deleted_at.is_(None),
                Workspace.status == "active",
                WorkspaceMember.user_id == self.actor_id,
                User.status == "active",
                GenerationRun.status.in_(("queued", "running")),
            )
        )
        return dict(rows.all())

    @logged
    async def list_conversations(
        self,
        workspace_id: UUID,
        *,
        limit: int = 30,
        cursor: str | None = None,
        status: str = "active",
        q: str | None = None,
    ) -> Page[Conversation]:
        """최근 메시지 시각과 UUID의 내림차순으로 조회하며 커서는 권한을 부여하지 않는다."""
        workspace_id, limit = identifier(workspace_id), page_limit(limit)
        if status not in ("active", "archived", "all"):
            raise InvalidInput("대화 상태가 올바르지 않습니다.")
        if q is not None:
            if not isinstance(q, str):
                raise InvalidInput("검색어는 문자열이어야 합니다.")
            q = required_text(q, 200, "검색어") if q.strip() else None
        position = None if cursor is None else decode_cursor(cursor)
        await self.access.require_membership(workspace_id)
        statement = select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.deleted_at.is_(None),
            self.access.exists(workspace_id),
        )
        if status != "all":
            statement = statement.where(Conversation.status == status)
        if q:
            # 부분검색의 와일드카드는 리터럴로 처리하고 메시지 수에 따른 중복 결과를 막는다.
            matching_message = exists(
                select(Message.id).where(
                    Message.workspace_id == Conversation.workspace_id,
                    Message.conversation_id == Conversation.id,
                    Message.content.icontains(q, autoescape=True),
                )
            )
            statement = statement.where(
                or_(Conversation.title.icontains(q, autoescape=True), matching_message)
            )
        if position is not None:
            statement = statement.where(
                tuple_(Conversation.last_message_at, Conversation.id) < tuple_(*position)
            )
        statement = (
            statement.order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
        rows = list((await self.session.scalars(statement)).all())
        items = rows[:limit]
        return Page(items, encode_cursor(items[-1]) if len(rows) > limit else None)

    async def _require_no_active_generation(self, conversation_id: UUID) -> None:
        if await self.active_generation_ids([conversation_id]):
            raise Conflict("응답 생성 중에는 대화를 보관하거나 삭제할 수 없습니다.")

    @logged
    async def search_matches(
        self, workspace_id: UUID, conversation_ids: list[UUID], query: str
    ) -> dict[UUID, dict]:
        """허용된 검색 결과마다 가장 최근 일치 메시지의 짧은 원문과 이동 위치를 반환한다."""
        workspace_id = identifier(workspace_id)
        query = required_text(query, 200, "검색어")
        await self.access.require_membership(workspace_id)
        if not conversation_ids:
            return {}
        # 전체 원문을 응답에 싣지 않고 일치 위치 앞 40자부터 최대 280자만 가져온다.
        start = func.greatest(func.strpos(func.lower(Message.content), func.lower(query)) - 40, 1)
        ranked = (
            select(
                Message.id,
                Message.conversation_id,
                Message.sequence,
                Message.role,
                func.substr(Message.content, start, 280).label("snippet"),
                start.label("snippet_start"),
                func.length(Message.content).label("content_length"),
                func.row_number()
                .over(partition_by=Message.conversation_id, order_by=Message.sequence.desc())
                .label("position"),
            )
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.workspace_id == workspace_id,
                Message.workspace_id == workspace_id,
                Conversation.id.in_([identifier(value) for value in conversation_ids]),
                Conversation.deleted_at.is_(None),
                self.access.exists(workspace_id),
                Message.content.icontains(query, autoescape=True),
            )
            .subquery()
        )
        rows = (await self.session.execute(select(ranked).where(ranked.c.position == 1))).all()
        return {
            row.conversation_id: {
                "message_id": row.id,
                "sequence": row.sequence,
                "role": row.role,
                "snippet": ("…" if row.snippet_start > 1 else "")
                + row.snippet
                + ("…" if row.snippet_start + len(row.snippet) - 1 < row.content_length else ""),
            }
            for row in rows
        }

    @logged
    async def update_conversation(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        title: str | None = None,
        is_pinned: bool | None = None,
        status: str | None = None,
    ) -> Conversation:
        """작성자 또는 작업 공간 관리자만 제목·고정·보관 상태를 변경한다."""
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        if title is not None:
            title = required_text(title, 300, "대화 제목")
        if is_pinned is not None and type(is_pinned) is not bool:
            raise InvalidInput("대화 고정 여부는 참 또는 거짓이어야 합니다.")
        if status is not None and status not in ("active", "archived"):
            raise InvalidInput("대화 상태가 올바르지 않습니다.")
        if title is None and is_pinned is None and status is None:
            raise InvalidInput("변경할 대화 정보가 필요합니다.")
        conversation = await self.access.lock_editable_conversation(workspace_id, conversation_id)
        if status == "archived":
            await self._require_no_active_generation(conversation_id)
        if title is not None:
            conversation.title = title
        if is_pinned is not None:
            conversation.is_pinned = is_pinned
        if status is not None:
            conversation.status = status
        await self.session.flush()
        return conversation

    async def mark_deleted(self, workspace_id: UUID, conversation_id: UUID) -> Conversation:
        """삭제 권한과 진행 작업을 검사하고 대화만 표시한다. 연관 정리는 서비스가 함께 수행한다."""
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        conversation = await self.access.lock_editable_conversation(workspace_id, conversation_id)
        await self._require_no_active_generation(conversation_id)
        conversation.deleted_at = datetime.now(UTC)
        return conversation
