from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class AuthIdentity(IdentityTimestamps, Base):
    """사용자 기본 정보와 분리한 비밀번호 로그인 수단."""

    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("user_id", "provider"),
        CheckConstraint("provider = 'password'", name="provider"),
        CheckConstraint(r"password_hash ~ '^\$argon2id\$'", name="password_hash_format"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="password", server_default=text("'password'")
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class AuthSession(IdentityTimestamps, Base):
    """원문 토큰을 저장하지 않는 절대 24시간 로그인 세션."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_format"),
        CheckConstraint("expires_at = created_at + interval '24 hours'", name="absolute_lifetime"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="revoked_at"),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now() + interval '24 hours'")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
