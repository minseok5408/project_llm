import pytest
from pydantic import ValidationError

from backend.app.config import Settings


def database_settings(**overrides) -> Settings:
    values = {
        "database_enabled": False,
        "database_url": None,
        "migration_database_url": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_database_is_optional_until_explicitly_enabled() -> None:
    assert database_settings().database_url is None
    assert database_settings(database_url="").database_url is None
    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        database_settings(database_enabled=True)


@pytest.mark.parametrize("field", ["database_url", "migration_database_url"])
@pytest.mark.parametrize(
    "invalid_url",
    [
        "sqlite:///private-secret.db",
        "postgresql://user:private-secret@localhost/db",
        "postgresql+asyncpg://user:private-secret@localhost:invalid/db",
        "postgresql+asyncpg://user:private-secret@localhost/",
    ],
)
def test_invalid_database_urls_are_rejected_without_leaking_secrets(field, invalid_url) -> None:
    with pytest.raises(ValidationError) as caught:
        database_settings(**{field: invalid_url})
    for output in (str(caught.value), repr(caught.value.errors()), caught.value.json()):
        assert "private-secret" not in output
    assert "postgresql+asyncpg" in str(caught.value)


def test_database_credentials_are_masked_in_configuration_output() -> None:
    settings = database_settings(
        database_enabled=True,
        database_url="postgresql+asyncpg://runtime:runtime-secret@localhost/qwen",
        migration_database_url="postgresql+asyncpg://owner:migration-secret@localhost/qwen",
    )
    for output in (repr(settings), str(settings.model_dump()), settings.model_dump_json()):
        assert "runtime-secret" not in output
        assert "migration-secret" not in output
