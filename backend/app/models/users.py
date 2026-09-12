from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class User(IdentityTimestamps, Base):
    """인증 수단과 독립된 사용자 기본 정보."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("memory_revision >= 0", name="memory_revision"),
        CheckConstraint("email = lower(btrim(email))", name="email_normalized"),
        CheckConstraint("char_length(email) BETWEEN 3 AND 320", name="email_length"),
        CheckConstraint(
            r"email ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'",
            name="email_format",
        ),
        CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 200", name="display_name_length"
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        CheckConstraint("platform_role IN ('member', 'system')", name="platform_role"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    # 서비스 전체 역할이며 작업 공간의 owner/admin/member 권한과 별도로 관리한다.
    platform_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member", server_default=text("'member'")
    )
    memory_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("email")
    def normalize_email(self, key: str, value: str) -> str:
        """ORM 입력을 정규화하고, 직접 SQL 입력은 DB 제약조건으로 검사한다."""
        return value.strip().lower()
