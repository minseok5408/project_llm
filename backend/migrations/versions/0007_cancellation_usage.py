"""기존 사용 이력을 보존하고 토큰 정산의 산정 기준을 기록한다."""

import sqlalchemy as sa
from alembic import op

revision = "0007_cancellation_usage"
down_revision = "0006_monthly_allowances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("token_reservations", sa.Column("usage_basis", sa.String(20), nullable=True))
    # 기존 확정 사용량과 반환 기록의 수치·시각은 변경하지 않는다.
    op.execute(
        "UPDATE token_reservations SET usage_basis = CASE "
        "WHEN status = 'settled' THEN 'provider' "
        "WHEN status = 'released' THEN 'waived' END "
        "WHERE status IN ('settled', 'released')"
    )
    op.create_check_constraint(
        op.f("ck_token_reservations_usage_basis"),
        "token_reservations",
        "usage_basis IN ('provider', 'received', 'waived')",
    )
    op.create_check_constraint(
        op.f("ck_token_reservations_usage_basis_state"),
        "token_reservations",
        "(status = 'reserved' AND usage_basis IS NULL) OR "
        "(status = 'settled' AND usage_basis IS NOT NULL "
        "AND (usage_basis != 'waived' OR (input_tokens = 0 AND output_tokens = 0))) OR "
        "(status = 'released' AND usage_basis IS NOT NULL AND usage_basis = 'waived')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_token_reservations_usage_basis_state"), "token_reservations", type_="check"
    )
    op.drop_constraint(
        op.f("ck_token_reservations_usage_basis"), "token_reservations", type_="check"
    )
    op.drop_column("token_reservations", "usage_basis")
