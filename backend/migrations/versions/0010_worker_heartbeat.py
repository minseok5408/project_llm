"""독립 실행자의 관측 상태를 단일 행에 저장한다."""

import sqlalchemy as sa
from alembic import op

revision = "0010_worker_heartbeat"
down_revision = "0009_context_compaction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_id", sa.UUID(), nullable=True),
        sa.CheckConstraint("name = 'generation'", name=op.f("ck_worker_heartbeats_singleton")),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generation_runs.id"],
            ondelete="SET NULL",
            name=op.f("fk_worker_heartbeats_generation_id_generation_runs"),
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_worker_heartbeats")),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
