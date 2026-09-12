"""사용자가 직접 저장한 개인 기억을 대화와 별도로 보관한다."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class UserMemory(IdentityTimestamps, Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "key"),
        CheckConstraint("char_length(btrim(key)) BETWEEN 1 AND 80", name="key_length"),
        CheckConstraint("char_length(btrim(content)) BETWEEN 1 AND 500", name="content_length"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
