"""질문 원문을 재사용하는 답변 버전과 현재 답변의 유일성을 추가한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_answer_versions"
down_revision = "0010_worker_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "generation_runs",
        sa.Column("supersedes_generation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_generation_runs_supersedes_generation_id_generation_runs",
        "generation_runs",
        "generation_runs",
        ["supersedes_generation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "distinct_predecessor",
        "generation_runs",
        "supersedes_generation_id IS NULL OR supersedes_generation_id <> id",
    )
    op.drop_constraint("uq_generation_runs_user_message_id", "generation_runs", type_="unique")
    op.create_index(
        "uq_generation_runs_current_question",
        "generation_runs",
        ["user_message_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    # 이전 스키마는 여러 답변을 표현하지 못하므로 원문과 사용량을 삭제하지 않고 중단한다.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM generation_runs "
            "WHERE supersedes_generation_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("재생성 답변이 존재하므로 답변 버전 마이그레이션을 되돌릴 수 없습니다.")
    op.drop_index("uq_generation_runs_current_question", table_name="generation_runs")
    op.create_unique_constraint(
        "uq_generation_runs_user_message_id", "generation_runs", ["user_message_id"]
    )
    op.drop_constraint(
        op.f("ck_generation_runs_distinct_predecessor"), "generation_runs", type_="check"
    )
    op.drop_constraint(
        "fk_generation_runs_supersedes_generation_id_generation_runs",
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_column("generation_runs", "supersedes_generation_id")
    op.drop_column("generation_runs", "is_current")
