"""질문 카드와 실행자가 관리하는 진행 목록을 생성 이력에 보존한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_question_progress"
down_revision = "0014_memory_recall"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column(
            "progress", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column(
        "generation_runs",
        sa.Column("question_card", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_generation_runs_progress"), "generation_runs", "jsonb_typeof(progress) = 'array'"
    )
    op.create_check_constraint(
        op.f("ck_generation_runs_question_card"),
        "generation_runs",
        "question_card IS NULL OR jsonb_typeof(question_card) = 'object'",
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM generation_runs WHERE question_card IS NOT NULL)")
    ):
        raise RuntimeError(
            "질문 카드 응답 이력이 있어 자동 롤백할 수 없습니다. 백업을 먼저 확인하세요."
        )
    for name in ("question_card", "progress"):
        op.drop_constraint(op.f(f"ck_generation_runs_{name}"), "generation_runs", type_="check")
        op.drop_column("generation_runs", name)
