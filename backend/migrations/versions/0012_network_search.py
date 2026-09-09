"""사용자 외부 통신 정책, 요청별 모드와 검색 출처 기록을 추가한다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_network_search"
down_revision = "0011_answer_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("local_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_user_preferences_revision")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
    )
    # 이전에 접수한 질문은 외부 검색에 동의한 이력이 없어 모두 로컬 전용으로 보존한다.
    op.add_column(
        "generation_runs",
        sa.Column("network_mode", sa.String(10), server_default=sa.text("'local'"), nullable=False),
    )
    op.add_column(
        "generation_runs",
        sa.Column("network_revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "generation_runs",
        sa.Column("web_search_mode", sa.String(8), server_default=sa.text("'off'"), nullable=False),
    )
    op.create_check_constraint(
        "network_mode", "generation_runs", "network_mode IN ('auto','local')"
    )
    op.create_check_constraint("network_revision", "generation_runs", "network_revision >= 0")
    op.create_check_constraint(
        "web_search_mode", "generation_runs", "web_search_mode IN ('auto','on','off')"
    )
    op.create_table(
        "web_search_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.func.gen_random_uuid(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(60), nullable=True),
        sa.Column("sources", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','searching','completed','disabled','unavailable',"
            "'failed','no_results','cancelled','omitted')",
            name=op.f("ck_web_search_runs_status"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sources) = 'array'", name=op.f("ck_web_search_runs_sources_array")
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["generation_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_web_search_runs_generation_id_generation_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_search_runs")),
        sa.UniqueConstraint("generation_id", name=op.f("uq_web_search_runs_generation_id")),
    )


def downgrade() -> None:
    # 출처는 이미 제공한 답변의 근거이므로 이전 스키마로 되돌리며 조용히 삭제하지 않는다.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM web_search_runs)")):
        raise RuntimeError(
            "검색 기록이 존재하므로 네트워크 검색 마이그레이션을 되돌릴 수 없습니다."
        )
    op.drop_table("web_search_runs")
    for name in ("web_search_mode", "network_revision", "network_mode"):
        op.drop_constraint(op.f(f"ck_generation_runs_{name}"), "generation_runs", type_="check")
        op.drop_column("generation_runs", name)
    op.drop_table("user_preferences")
