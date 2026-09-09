"""로컬 관리 명령의 접속 대상 제한을 검사한다."""

import pytest
from sqlalchemy.engine import make_url

from backend.app.repositories import InvalidInput
from scripts.manage_accounts import require_local_database


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@remote.example/db",
        "postgresql+asyncpg://u:p@localhost/db?host=remote.example:5432",
        "postgresql+asyncpg://u:p@localhost/db?host=localhost:5432&host=remote.example:5432",
        "postgresql+asyncpg://u:p@localhost/db?dsn=postgresql%3A%2F%2Fremote.example%2Fdb",
    ],
)
def test_admin_cli_rejects_remote_and_query_host_override(url):
    with pytest.raises(InvalidInput):
        require_local_database(make_url(url))


def test_admin_cli_allows_plain_loopback_connection():
    require_local_database(make_url("postgresql+asyncpg://u:p@127.0.0.1:5432/db"))
