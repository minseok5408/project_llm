"""검증된 사용자와 워크스페이스 소속을 기준으로 데이터를 조회하고 저장한다.

공개 HTTP API가 아닌 내부 저장소 계약이다. 사용자 식별자는 신뢰할 수 있는 호출자가
제공해야 한다. 모든 쓰기는 flush만 수행하며 commit과 SQL 오류 이후 rollback은
호출자가 책임진다. 인증, 사용자 입력 역할의 신뢰 판단, 생성 큐는 여기서 구현하지 않는다.
"""

import base64
import binascii
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Conversation, Message, User, Workspace, WorkspaceMember
from backend.app.repositories.errors import (
    AccessDenied,
    Conflict,
    InvalidInput,
    RepositoryError,
    RepositoryUnavailable,
)
from backend.app.repositories.types import Page, UserWorkspace

logger = logging.getLogger(__name__)


def logged[**P, T](operation: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """고정된 작업명과 결과, 소요시간, UUID만 기록하고 DB 예외를 안전하게 바꾼다."""

    @wraps(operation)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        started = perf_counter()
        result = "error"
        try:
            value = await operation(*args, **kwargs)
            result = "ok"
            return value
        except IntegrityError:
            result = "conflict"
            raise Conflict("데이터 제약조건으로 작업을 완료할 수 없습니다.") from None
        except SQLAlchemyError:
            result = "unavailable"
            raise RepositoryUnavailable("데이터베이스 작업을 완료할 수 없습니다.") from None
        except RepositoryError as error:
            result = type(error).__name__
            raise
        finally:
            record: dict[str, str | float] = {
                "event": "repository_operation",
                "operation": operation.__name__,
                "result": result,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            }
            workspace_id = kwargs.get("workspace_id")
            if workspace_id is None and len(args) > 1:
                workspace_id = args[1]
            if isinstance(workspace_id, UUID):
                record["workspace_id"] = str(workspace_id)
            logger.info(json.dumps(record, separators=(",", ":")))

    return wrapped


def identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidInput("식별자는 UUID여야 합니다.")
    return value


def required_text(value: str, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > maximum
        or "\x00" in value
    ):
        raise InvalidInput(f"{field} 값의 형식이나 길이가 올바르지 않습니다.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidInput(f"{field} 값의 문자 인코딩이 올바르지 않습니다.") from None
    return value.strip()


def optional_text(value: str | None, field: str) -> str | None:
    return None if value is None else required_text(value, 255, field)


def page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise InvalidInput("페이지 크기는 1부터 100까지의 정수여야 합니다.")
    return value


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise InvalidInput("대화 목록 커서가 올바르지 않습니다.")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        parts = json.loads(decoded)
        if not isinstance(parts, list) or len(parts) != 2:
            raise ValueError
        timestamp = datetime.fromisoformat(parts[0])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(UTC), UUID(parts[1])
    except (ValueError, TypeError, AttributeError, OverflowError, binascii.Error, UnicodeError):
        raise InvalidInput("대화 목록 커서가 올바르지 않습니다.") from None


def encode_cursor(conversation: Conversation) -> str:
    position = [conversation.last_message_at.astimezone(UTC).isoformat(), str(conversation.id)]
    return base64.urlsafe_b64encode(json.dumps(position).encode()).decode().rstrip("=")


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


class Repository:
    """신뢰할 수 있는 호출자가 제공한 actor_id로 활성 사용자·소속을 매번 SQL에서 확인한다."""

    def __init__(self, session: AsyncSession, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = identifier(actor_id)

    def _membership_query(self, workspace_id: UUID):
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

    def _access_exists(self, workspace_id: UUID):
        return exists(self._membership_query(workspace_id))

    async def _require_membership(
        self, workspace_id: UUID, *, lock: bool = False
    ) -> WorkspaceMember:
        statement = self._membership_query(workspace_id).execution_options(populate_existing=True)
        if lock:
            # 쓰기 트랜잭션 중 계정 비활성화·소속 철회·역할 변경이 먼저 확정되지 않게 한다.
            statement = statement.with_for_update(read=True, of=(User, Workspace, WorkspaceMember))
        membership = await self.session.scalar(statement)
        if membership is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return membership

    def _conversation_query(self, workspace_id: UUID, conversation_id: UUID):
        return (
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self._access_exists(workspace_id),
            )
            .execution_options(populate_existing=True)
        )

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
            .where(Workspace.id == workspace_id, self._access_exists(workspace_id))
            .execution_options(populate_existing=True)
        )
        if workspace is None:
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return workspace

    @logged
    async def create_conversation(
        self,
        workspace_id: UUID,
        *,
        model: str,
        title: str = "새 대화",
        settings: dict[str, Any] | None = None,
    ) -> Conversation:
        workspace_id = identifier(workspace_id)
        title = required_text(title, 300, "대화 제목")
        model = required_text(model, 255, "모델")
        if settings is not None and not isinstance(settings, dict):
            raise InvalidInput("대화 설정은 JSON 객체여야 합니다.")
        try:
            normalized_settings = json.loads(json.dumps(settings or {}, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise InvalidInput("대화 설정을 JSON으로 변환할 수 없습니다.") from None
        await self._require_membership(workspace_id, lock=True)
        conversation = Conversation(
            workspace_id=workspace_id,
            created_by=self.actor_id,
            title=title,
            model=model,
            settings=normalized_settings,
        )
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    @logged
    async def get_conversation(self, workspace_id: UUID, conversation_id: UUID) -> Conversation:
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        conversation = await self.session.scalar(
            self._conversation_query(workspace_id, conversation_id)
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
        await self._require_membership(workspace_id)
        statement = select(Conversation).where(
            Conversation.workspace_id == workspace_id,
            Conversation.deleted_at.is_(None),
            self._access_exists(workspace_id),
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

    @logged
    async def soft_delete_conversation(self, workspace_id: UUID, conversation_id: UUID) -> None:
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        conversation = await self._lock_editable_conversation(workspace_id, conversation_id)
        await self._require_no_active_generation(conversation_id)
        conversation.deleted_at = datetime.now(UTC)
        await self.session.flush()

    async def _lock_editable_conversation(
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
        membership = await self._require_membership(workspace_id, lock=True)
        conversation = await self.session.scalar(
            self._conversation_query(workspace_id, conversation_id).with_for_update(of=Conversation)
        )
        if conversation is None or (
            conversation.created_by != self.actor_id and membership.role not in ("owner", "admin")
        ):
            raise AccessDenied("대상에 접근할 수 없습니다.")
        return conversation

    async def _require_no_active_generation(self, conversation_id: UUID) -> None:
        if await self.active_generation_ids([conversation_id]):
            raise Conflict("응답 생성 중에는 대화를 보관하거나 삭제할 수 없습니다.")

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
        conversation = await self._lock_editable_conversation(workspace_id, conversation_id)
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
        prompt_version: str | None = None,
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
        model, prompt_version = (
            optional_text(model, "모델"),
            optional_text(prompt_version, "프롬프트 버전"),
        )
        await self._require_membership(workspace_id, lock=True)
        sequence = await self.session.scalar(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self._access_exists(workspace_id),
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
            prompt_version=prompt_version,
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
    ) -> Page[Message]:
        """최신 순으로 한 페이지를 찾은 뒤 시간순으로 반환한다. 다음 before는 최소 순번이다."""
        workspace_id, conversation_id = identifier(workspace_id), identifier(conversation_id)
        limit = page_limit(limit)
        if before is not None and (type(before) is not int or not 1 <= before <= 2**63 - 1):
            raise InvalidInput("메시지 커서는 양의 64비트 정수여야 합니다.")
        await self.get_conversation(workspace_id, conversation_id)
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.workspace_id == workspace_id,
                Message.conversation_id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.deleted_at.is_(None),
                self._access_exists(workspace_id),
            )
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
        return Page(items, items[0].sequence if len(rows) > limit else None)
