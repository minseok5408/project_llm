"""검색 시도의 상태와 답변에 사용한 출처를 생성 작업에 연결한다."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.base import IdentityTimestamps


class WebSearchRun(IdentityTimestamps, Base):
    __tablename__ = "web_search_runs"
    __table_args__ = (
        UniqueConstraint("generation_id"),
        CheckConstraint(
            "status IN ('pending','searching','completed','disabled','unavailable',"
            "'failed','no_results','cancelled','omitted')",
            name="status",
        ),
        CheckConstraint("jsonb_typeof(sources) = 'array'", name="sources_array"),
    )
    generation_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(60))
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
