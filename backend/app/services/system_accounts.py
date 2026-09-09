"""로컬 관리 명령에서만 명시적으로 호출하는 시스템 계정 초기화."""

import json
import logging
import re

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import AuthSession, User
from backend.app.repositories import AccessDenied, InvalidInput, create_user_with_workspace
from backend.app.repositories.core import logged, required_text

logger = logging.getLogger(__name__)


@logged
async def bootstrap_system_user(
    session: AsyncSession, *, email: str, display_name: str = "system"
) -> User:
    """로컬 운영자가 지정한 계정을 생성하거나 승격하며 커밋은 호출자가 수행한다.

    공개 회원가입이나 앱 시작 과정에서 호출하지 않는다. 이메일·표시 이름 자체에는
    권한이 없으며, 기존 비활성 계정은 이 명령으로 다시 활성화하지 않는다.
    """
    normalized_email = required_text(email, 320, "이메일").lower()
    if (
        len(normalized_email) > 320
        or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized_email) is None
    ):
        raise InvalidInput("이메일 형식이 올바르지 않습니다.")
    normalized_name = required_text(display_name, 200, "표시 이름")
    # 두 초기화 명령이 동시에 같은 계정과 기본 작업 공간을 만들지 않게 한다.
    await session.execute(text("SELECT pg_advisory_xact_lock(7160524630128421)"))
    user = await session.scalar(
        select(User)
        .where(User.email == normalized_email)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        created = await create_user_with_workspace(
            session, email=normalized_email, display_name=normalized_name
        )
        user = created.user
        action = "created"
    else:
        if user.status != "active":
            raise AccessDenied("비활성 계정은 시스템 계정으로 초기화할 수 없습니다.")
        action = "unchanged" if user.platform_role == "system" else "promoted"
    user.platform_role = "system"
    if action == "promoted":
        # 권한 승격 전에 발급된 세션으로 새 시스템 권한을 사용하지 못하게 한다.
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=func.clock_timestamp())
        )
    await session.flush()
    logger.info(
        json.dumps(
            {
                "event": "system_account_bootstrap",
                "user_id": str(user.id),
                "action": action,
                "source": "local_admin_command",
                "transaction": "pending_commit",
            },
            separators=(",", ":"),
        )
    )
    return user
