"""본인이 명시적으로 저장한 기억만 관리하며 변경한 기억의 파생 문맥을 무효화한다."""

from uuid import UUID

from sqlalchemy import select, update

from backend.app.models import GenerationRun, User, UserMemory
from backend.app.repositories import AccessDenied, Conflict, InvalidInput, Repository
from backend.app.repositories.validation import required_text

MEMORY_LIMIT = 20


class MemoryService:
    def __init__(self, session, actor_id: UUID):
        self.session, self.actor_id = session, actor_id

    async def snapshot(self) -> dict:
        user = await Repository(self.session, self.actor_id).get_user()
        rows = await self.session.scalars(
            select(UserMemory)
            .where(UserMemory.user_id == self.actor_id)
            .order_by(UserMemory.key, UserMemory.id)
        )
        return {
            "revision": user.memory_revision,
            "limit": MEMORY_LIMIT,
            "items": [
                {
                    "id": str(row.id),
                    "key": row.key,
                    "content": row.content,
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ],
        }

    async def change(
        self,
        *,
        revision: int,
        key: str | None = None,
        content: str | None = None,
        memory_id: UUID | None = None,
        delete: bool = False,
    ) -> dict:
        # 사용자 잠금이 생성 승인·정산과 같은 순서이므로 동시 저장도 한 세대씩 반영한다.
        user = await self.session.scalar(
            select(User)
            .where(User.id == self.actor_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or user.status != "active":
            raise AccessDenied("기억에 접근할 수 없습니다.")
        row = None
        if memory_id is not None:
            row = await self.session.scalar(
                select(UserMemory).where(
                    UserMemory.id == memory_id, UserMemory.user_id == self.actor_id
                )
            )
            if row is None:
                raise AccessDenied("기억을 찾을 수 없습니다.")
        if type(revision) is not int or revision != user.memory_revision:
            raise Conflict("기억이 변경되었습니다. 새로고침 후 다시 저장하세요.")
        if delete:
            if row is None:
                raise InvalidInput("삭제할 기억이 필요합니다.")
            await self.session.delete(row)
        else:
            key = required_text(key, 80, "기억 이름").casefold()
            content = required_text(content, 500, "기억 내용")
            duplicate = await self.session.scalar(
                select(UserMemory).where(UserMemory.user_id == self.actor_id, UserMemory.key == key)
            )
            if duplicate is not None and duplicate.id != memory_id:
                raise Conflict("같은 이름의 기억이 있습니다. 기존 기억을 수정하세요.")
            if row is None:
                count = len(
                    (
                        await self.session.scalars(
                            select(UserMemory.id).where(UserMemory.user_id == self.actor_id)
                        )
                    ).all()
                )
                if count >= MEMORY_LIMIT:
                    raise Conflict("기억은 최대 20개까지 저장할 수 있습니다.")
                self.session.add(UserMemory(user_id=self.actor_id, key=key, content=content))
            else:
                if (row.key, row.content) == (key, content):
                    return await self.snapshot()
                row.key, row.content = key, content
        user.memory_revision += 1
        # 변경 전 기억을 읽은 대기·진행 작업은 다른 프로세스에서도 중단 신호를 관찰한다.
        await self.session.execute(
            update(GenerationRun)
            .where(
                GenerationRun.status.in_(("queued", "running")),
                (GenerationRun.user_id == self.actor_id)
                | GenerationRun.memory_dependencies.has_key(str(self.actor_id)),
            )
            .values(cancel_requested=True)
        )
        await self.session.flush()
        return await self.snapshot()
