"""메시지 역할 검증·순번 확보·페이지 조회를 담당한다."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update

from backend.app.models import Conversation, Message
from backend.app.repositories.access import RepositoryAccess
from backend.app.repositories.conversations import ConversationRepository
from backend.app.repositories.errors import AccessDenied, InvalidInput
from backend.app.repositories.operations import logged
from backend.app.repositories.types import Page
from backend.app.repositories.validation import identifier, optional_text, page_limit


@dataclass(frozen=True, slots=True)
class MessageWindow(Page[Message]):
    """검색 위치에서 앞뒤로 읽을 수 있도록 이후 메시지 커서도 보존한다."""

    newer_cursor: int | None = None


class MessageRepository:
    """같은 대화 저장소와 권한 계약으로 원문 저장·조회를 처리한다."""

    def __init__(self, access: RepositoryAccess, conversations: ConversationRepository):
        self.access, self.conversations = access, conversations
        self.session, self.actor_id = access.session, access.actor_id

    @logged
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
        """내부의 신뢰된 역할만 받는다. 외부 요청의 role을 그대로 전달하면 안 된다.

        user 작성자는 현재 사용자로 고정하고 assistant/system 작성자는 비운다.
        순번과 최근 메시지 시각은 같은 UPDATE로 확보하며 실패 시 호출자가 롤백한다.
        """
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        if role not in ("user", "assistant", "system"):
            raise InvalidInput("메시지 역할이 올바르지 않습니다.")
        if status not in ("pending", "completed", "failed", "cancelled"):
            raise InvalidInput("메시지 상태가 올바르지 않습니다.")
        if role == "user" and status != "completed":
            raise InvalidInput("사용자 메시지는 완료 상태여야 합니다.")
        if (
            not isinstance(content, str)
            or "\x00" in content
            or (status == "completed" and not content.strip())
        ):
            raise InvalidInput("메시지 내용이 올바르지 않습니다.")
        try:
            content.encode("utf-8")
        except UnicodeError:
            raise InvalidInput("메시지 내용의 문자 인코딩이 올바르지 않습니다.") from None
        if token_count is not None and (
            type(token_count) is not int or not 0 <= token_count <= 2**63 - 1
        ):
            raise InvalidInput("토큰 수는 0 이상의 64비트 정수여야 합니다.")
        model = optional_text(model, "모델")
        await self.access.require_membership(workspace_id, lock=True)
        sequence = await self.session.scalar(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self.access.exists(workspace_id),
            )
            .values(
                next_message_sequence=Conversation.next_message_sequence + 1,
                last_message_at=func.clock_timestamp(),
                updated_at=func.clock_timestamp(),
            )
            .returning(Conversation.next_message_sequence - 1)
            .execution_options(synchronize_session=False)
        )
        if sequence is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        message = Message(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            created_by=self.actor_id if role == "user" else None,
            content=content,
            status=status,
            token_count=token_count,
            model=model,
        )
        self.session.add(message)
        await self.session.flush()
        return message

    @logged
    async def list_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        *,
        limit: int = 50,
        before: int | None = None,
        after: int | None = None,
        around: UUID | None = None,
    ) -> MessageWindow:
        """최신 순으로 한 페이지를 찾은 뒤 시간순으로 반환한다. 다음 before는 최소 순번이다."""
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        limit = page_limit(limit)
        for cursor in (before, after):
            if cursor is not None and (type(cursor) is not int or not 1 <= cursor <= 2**63 - 1):
                raise InvalidInput("메시지 커서는 양의 64비트 정수여야 합니다.")
        if sum(value is not None for value in (before, after, around)) > 1:
            raise InvalidInput("메시지 조회 방향은 하나만 지정해야 합니다.")
        await self.conversations.get_conversation(workspace_id, conversation_id)
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.workspace_id == workspace_id,
                Message.conversation_id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self.access.exists(workspace_id),
            )
        )
        if around is not None:
            target = await self.session.scalar(statement.where(Message.id == identifier(around)))
            if target is None:
                raise AccessDenied("대상에 접근할 수 없습니다.")
            left_limit, right_limit = (limit + 1) // 2, limit // 2
            left = list(
                (
                    await self.session.scalars(
                        statement.where(Message.sequence <= target.sequence)
                        .order_by(Message.sequence.desc())
                        .limit(left_limit + 1)
                    )
                ).all()
            )
            right = list(
                (
                    await self.session.scalars(
                        statement.where(Message.sequence > target.sequence)
                        .order_by(Message.sequence)
                        .limit(right_limit + 1)
                    )
                ).all()
            )
            items = list(reversed(left[:left_limit])) + right[:right_limit]
            return MessageWindow(
                items,
                items[0].sequence if len(left) > left_limit else None,
                items[-1].sequence if len(right) > right_limit else None,
            )
        if after is not None:
            rows = list(
                (
                    await self.session.scalars(
                        statement.where(Message.sequence > after)
                        .order_by(Message.sequence)
                        .limit(limit + 1)
                    )
                ).all()
            )
            items = rows[:limit]
            has_older = bool(items) and await self.session.scalar(
                select(statement.where(Message.sequence < items[0].sequence).exists())
            )
            return MessageWindow(
                items,
                items[0].sequence if has_older else None,
                items[-1].sequence if len(rows) > limit else None,
            )
        if before is not None:
            statement = statement.where(Message.sequence < before)
        statement = (
            statement.order_by(Message.sequence.desc())
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
        rows = list((await self.session.scalars(statement)).all())
        items = list(reversed(rows[:limit]))
        has_newer = (
            before is not None
            and bool(items)
            and await self.session.scalar(
                select(Message.id)
                .where(
                    Message.workspace_id == workspace_id,
                    Message.conversation_id == conversation_id,
                    Message.sequence > items[-1].sequence,
                    self.access.exists(workspace_id),
                )
                .limit(1)
            )
        )
        return MessageWindow(
            items,
            items[0].sequence if len(rows) > limit else None,
            items[-1].sequence if has_newer else None,
        )
