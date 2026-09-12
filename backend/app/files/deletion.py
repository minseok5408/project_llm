"""대화 삭제 트랜잭션에서 검색 조각을 제거하고 원본 삭제 대상을 표시한다."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Chunk, Document, DocumentVersion


async def mark_conversation_files_deleted(
    session: AsyncSession, conversation_id: UUID, deleted_at: datetime
) -> None:
    """권한·대화 잠금을 확보한 삭제 서비스 안에서만 호출하며 커밋하지 않는다."""
    document_ids = select(Document.id).where(Document.conversation_id == conversation_id)
    versions = select(DocumentVersion.id).where(DocumentVersion.document_id.in_(document_ids))
    await session.execute(delete(Chunk).where(Chunk.version_id.in_(versions)))
    await session.execute(
        update(Document)
        .where(Document.conversation_id == conversation_id)
        .values(deleted_at=deleted_at, filename="삭제된 파일")
    )
