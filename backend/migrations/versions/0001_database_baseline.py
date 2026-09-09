"""PR 2에서 도메인 테이블을 추가하기 전에 마이그레이션 이력을 마련한다.

리비전 ID: 0001_database_baseline
이전 리비전:
"""

from collections.abc import Sequence

revision: str = "0001_database_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Alembic이 alembic_version을 생성하고 기록한다. 도메인 테이블은 아직 추가하지 않는다.
    pass


def downgrade() -> None:
    # Alembic이 기준 리비전을 지워 업그레이드와 다운그레이드를 반복할 수 있게 한다.
    pass
