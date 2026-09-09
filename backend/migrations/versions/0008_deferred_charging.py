"""기존 예약 이력은 보존하고 생성 완료 후 차감 방식을 추가한다."""

import sqlalchemy as sa
from alembic import op

revision = "0008_deferred_charging"
down_revision = "0007_cancellation_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 기존 예약량과 사용량은 변경하지 않고 모든 기존 기록을 사전 예약 방식으로 보존한다.
    op.add_column(
        "token_reservations",
        sa.Column("charge_mode", sa.String(20), nullable=False, server_default="reserved"),
    )
    op.create_check_constraint(
        op.f("ck_token_reservations_charge_mode"),
        "token_reservations",
        "charge_mode IN ('reserved', 'deferred')",
    )
    op.create_index(
        "ux_token_reservations_pending_deferred_user",
        "token_reservations",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("charge_mode = 'deferred' AND status = 'reserved'"),
    )


def downgrade() -> None:
    # 미정산 기록을 구형 예약으로 해석하면 존재하지 않는 예산 예약량을 반환하게 된다.
    # 해당 요청을 먼저 정산하거나 미사용으로 종료한 뒤에만 안전하게 되돌릴 수 있다.
    pending = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM token_reservations "
            "WHERE charge_mode = 'deferred' AND status = 'reserved')"
        )
    )
    if pending:
        raise RuntimeError(
            "완료 후 차감 요청을 먼저 정산하거나 종료해야 이전 버전으로 되돌릴 수 있습니다."
        )
    op.drop_index("ux_token_reservations_pending_deferred_user", table_name="token_reservations")
    op.drop_constraint(
        op.f("ck_token_reservations_charge_mode"), "token_reservations", type_="check"
    )
    op.drop_column("token_reservations", "charge_mode")
