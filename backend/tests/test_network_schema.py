"""외부 통신 정책의 DB 제약과 검색 근거를 보존하는 마이그레이션을 검증한다."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models.generations import GenerationRun
from backend.app.models.user_preferences import UserPreference
from backend.app.models.web_search import WebSearchRun
from backend.tests.conftest import run_alembic, version_rows
from backend.tests.test_generations import harness as harness

pytestmark = pytest.mark.postgres


async def test_preferences_reject_negative_revision_and_unknown_user(harness):
    for user_id, revision in ((harness.system.user_id, -1), (uuid4(), 0)):
        async with harness.database.session() as session:
            session.add(UserPreference(user_id=user_id, local_only=False, revision=revision))
            with pytest.raises(IntegrityError):
                await session.commit()


@pytest.mark.parametrize(
    "field,value", [("network_mode", "wifi"), ("web_search_mode", "yes"), ("network_revision", -1)]
)
async def test_generation_rejects_invalid_network_policy(harness, field, value):
    accepted = await harness.submit()
    async with harness.database.session() as session:
        row = await session.get(GenerationRun, UUID(accepted["id"]))
        setattr(row, field, value)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_network_downgrade_preserves_recorded_answer_sources(harness, postgres):
    accepted = await harness.submit()
    generation_id = UUID(accepted["id"])
    source = {"title": "검색 검증 출처", "url": "https://example.com/source", "snippet": "근거"}
    async with harness.database.session() as session:
        row = await session.scalar(
            select(WebSearchRun).where(WebSearchRun.generation_id == generation_id)
        )
        if row is None:
            row = WebSearchRun(generation_id=generation_id, provider="test")
            session.add(row)
        row.status = "completed"
        row.sources = [source]
        row.completed_at = datetime.now(UTC)
        await session.commit()
    await harness.database.dispose()
    await run_alembic(postgres, "downgrade", "0011_answer_versions", success=False)
    assert await version_rows(harness.database) == ["0017_schema_roles"]
    async with harness.database.session() as session:
        row = await session.scalar(
            select(WebSearchRun).where(WebSearchRun.generation_id == generation_id)
        )
        assert row.sources == [source]
        assert row.status == "completed"
