"""인증된 호출자가 사용할 내부 데이터 접근 계약."""

from backend.app.repositories.core import Repository, create_user_with_workspace
from backend.app.repositories.errors import (
    AccessDenied,
    Conflict,
    InvalidInput,
    RepositoryError,
    RepositoryUnavailable,
)
from backend.app.repositories.types import Page, UserWorkspace

__all__ = [
    "AccessDenied",
    "Conflict",
    "InvalidInput",
    "Page",
    "Repository",
    "RepositoryError",
    "RepositoryUnavailable",
    "UserWorkspace",
    "create_user_with_workspace",
]
