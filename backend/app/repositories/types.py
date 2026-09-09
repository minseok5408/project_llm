"""저장소 생성 결과와 페이지 조회 결과."""

from dataclasses import dataclass

from backend.app.models import User, Workspace, WorkspaceMember


@dataclass(frozen=True, slots=True)
class UserWorkspace:
    """한 트랜잭션에서 만든 사용자와 기본 워크스페이스, 소유자 소속."""

    user: User
    workspace: Workspace
    membership: WorkspaceMember


@dataclass(frozen=True, slots=True)
class Page[T]:
    """다음 대화 커서는 문자열이고 이전 메시지 커서는 정수 순번이다."""

    items: list[T]
    next_cursor: str | int | None
