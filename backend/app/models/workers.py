"""작업 재실행 판단과 분리된 독립 실행자 관측 정보."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class WorkerHeartbeat(Base):
    """리더 한 개의 최근 상태만 보존하며 시간 만료로 소유권을 넘기지 않는다."""

    __tablename__ = "worker_heartbeats"
    __table_args__ = (CheckConstraint("name = 'generation'", name="singleton"),)

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    worker_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True), ForeignKey("generation_runs.id", ondelete="SET NULL")
    )
