"""월 무료 예산과 별도 요금제 예산의 출처를 구분한다."""

import sqlalchemy as sa
from alembic import op

revision = "0006_monthly_allowances"
down_revision = "0005_generation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 기존 예산과 사용 이력은 그대로 두고 기존 예산의 출처만 plan으로 채운다.
    op.add_column(
        "token_budgets",
        sa.Column("source", sa.String(20), nullable=False, server_default=sa.text("'plan'")),
    )
    op.create_check_constraint(
        op.f("ck_token_budgets_source"), "token_budgets", "source IN ('plan', 'free_monthly')"
    )
    op.create_check_constraint(
        op.f("ck_token_budgets_free_plan_required"),
        "token_budgets",
        "source != 'free_monthly' OR plan_id IS NOT NULL",
    )


def downgrade() -> None:
    # 이전 코드는 무료·유료 예산의 동시 유효기간을 해석하지 못한다.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM token_budgets WHERE source = 'free_monthly')")
    ):
        raise RuntimeError("무료 예산의 사용·예약 이력을 보존할 롤백 계획이 필요합니다.")
    op.drop_constraint(op.f("ck_token_budgets_free_plan_required"), "token_budgets", type_="check")
    op.drop_constraint(op.f("ck_token_budgets_source"), "token_budgets", type_="check")
    op.drop_column("token_budgets", "source")
