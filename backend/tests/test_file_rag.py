"""실제 DB에서 첨부·검색·생성·삭제와 작업 공간 격리를 검증한다."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.app.context.builder import list_context_turns
from backend.app.context.dependencies import check_dependencies
from backend.app.files.parser import FileProcessingError
from backend.app.files.retrieval import candidates
from backend.app.files.service import FileService
from backend.app.models import (
    Chunk,
    ConversationCompaction,
    Document,
    DocumentVersion,
    GenerationRun,
    GenerationStep,
    WorkspaceMember,
)
from backend.app.repositories import AccessDenied, Conflict, Repository
from backend.tests.test_generations import harness, snapshot  # noqa: F401

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Embeddings:
    def __init__(self):
        self.calls = []

    async def split(self, chunks):
        return chunks

    async def encode(self, texts, *, query=False):
        self.calls.append((texts, query))
        return [[1.0] + [0.0] * 383 for _ in texts]


@pytest.fixture
async def files(harness, tmp_path):  # noqa: F811
    harness.settings.file_storage_path = tmp_path / "documents"
    harness.worker.files.storage.root = harness.settings.file_storage_path
    harness.worker.files.embeddings = Embeddings()
    return harness


async def upload(h, *, account=None, content="휴가 정책: 연차는 15일입니다.", conversation_id=None):
    account = account or h.system
    async with h.database.session() as session:
        return await FileService(session, account.user_id, h.settings).upload(
            conversation_id or account.conversation_id, "정책.txt", "text/plain", content.encode()
        )


async def remove(h, document_id, *, cleanup=True):
    async with h.database.session() as session:
        service = FileService(session, h.system.user_id, h.settings)
        await service.delete(h.system.conversation_id, UUID(document_id))
        await session.commit()
        if cleanup:
            await service.cleanup()
            await session.commit()


async def test_upload_dedup_index_and_acl_before_candidate(files):
    h = files
    first = await upload(h)
    assert (await upload(h))["id"] == first["id"]
    other = await upload(h, account=h.other)
    assert other["id"] != first["id"]
    assert await h.worker.files.process_next()
    assert await h.worker.files.process_next()
    async with h.database.session() as session:
        rows = await candidates(
            session, h.system.user_id, h.system.workspace_id, h.system.conversation_id
        )
        assert len(rows) == 1 and str(rows[0][0].id) == first["id"]
        with pytest.raises(AccessDenied):
            await candidates(
                session, h.other.user_id, h.system.workspace_id, h.system.conversation_id
            )
        conversation = await Repository(session, h.system.user_id).create_conversation(
            h.system.workspace_id, model=h.settings.llm_model_id
        )
        assert (
            await candidates(session, h.system.user_id, h.system.workspace_id, conversation.id)
            == []
        )
        conversation_id = conversation.id
        await session.commit()
    same_workspace = await upload(h, conversation_id=conversation_id, content="급여 지급일은 25일")
    assert await h.worker.files.process_next()
    async with h.database.session() as session:
        for target, expected in (
            (h.system.conversation_id, first["id"]),
            (conversation_id, same_workspace["id"]),
        ):
            rows = await candidates(session, h.system.user_id, h.system.workspace_id, target)
            assert len(rows) == 1 and str(rows[0][0].id) == expected


async def test_database_rejects_second_version_without_changing_original(files):
    h = files
    document = await upload(h)
    document_id, version_id = UUID(document["id"]), UUID(document["version_id"])
    async with h.database.session() as session:
        original = await session.get(DocumentVersion, version_id)
        with pytest.raises(IntegrityError, match="uq_document_versions_document_id"):
            async with session.begin_nested():
                session.add(
                    DocumentVersion(
                        document_id=document_id,
                        version=2,
                        sha256="f" * 64,
                        byte_size=10,
                        extension=".txt",
                    )
                )
                await session.flush()
        versions = list(
            await session.scalars(
                select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            )
        )
        assert versions == [original]
        service = FileService(session, h.system.user_id, h.settings)
        assert (await service.get(h.system.conversation_id, document_id))[1].id == version_id
        assert service.storage.read(h.system.workspace_id, document_id, version_id) == (
            "휴가 정책: 연차는 15일입니다.".encode()
        )


async def test_database_rejects_document_with_another_conversations_workspace(files):
    h = files
    async with h.database.session() as session:
        with pytest.raises(IntegrityError, match="fk_documents_workspace_id_conversations"):
            async with session.begin_nested():
                session.add(
                    Document(
                        workspace_id=h.other.workspace_id,
                        conversation_id=h.system.conversation_id,
                        uploaded_by=h.system.user_id,
                        filename="잘못된 범위.txt",
                    )
                )
                await session.flush()
        assert await session.scalar(select(func.count()).select_from(Document)) == 0


async def test_workspace_member_can_read_but_not_delete_another_members_file(files):
    h = files
    document = await upload(h)
    async with h.database.session() as session:
        session.add(
            WorkspaceMember(
                workspace_id=h.system.workspace_id, user_id=h.other.user_id, role="member"
            )
        )
        await session.commit()
        service = FileService(session, h.other.user_id, h.settings)
        page = await service.list(h.system.conversation_id)
        assert page["items"][0]["id"] == document["id"]
        assert page["items"][0]["can_delete"] is False
        with pytest.raises(AccessDenied):
            await service.delete(h.system.conversation_id, UUID(document["id"]))


async def test_unready_blocks_admission_then_injects_only_ephemeral_context(files):
    h = files
    document = await upload(h, content="휴가 정책: 연차는 15일입니다. UNIQUE_FILE_SECRET")
    with pytest.raises(Conflict):
        await h.submit(content="연차는 몇 일인가요?")
    await h.worker.files.process_next()
    accepted = await h.submit(content="연차는 몇 일인가요?")
    before = await snapshot(h.database, accepted["generation_id"])
    assert "UNIQUE_FILE_SECRET" not in str(before.run.request_messages)
    await h.execute_next()
    result = await snapshot(h.database, accepted["generation_id"])
    assert result.run.status == "completed", result.run.error_code
    assert result.run.file_sources[0]["document_id"] == document["id"]
    assert result.run.memory_dependencies[f"document:{document['id']}"] == 1
    assert "UNIQUE_FILE_SECRET" not in str([event.payload for event in result.events])
    assert result.run.request_messages == []
    async with h.database.session() as session:
        steps = list(
            (
                await session.scalars(
                    select(GenerationStep).where(GenerationStep.generation_id == result.run.id)
                )
            ).all()
        )
        assert any(step.name == "file_search" and step.status == "completed" for step in steps)
    assert result.reservation.status == "settled"


async def test_delete_removes_vectors_raw_and_invalidates_answers_summaries(files):
    h = files
    doc = await upload(h)
    await h.worker.files.process_next()
    run = await h.submit(content="연차 정책을 설명해줘")
    await h.execute_next()
    result = await snapshot(h.database, run["generation_id"])
    async with h.database.session() as session:
        session.add(
            ConversationCompaction(
                workspace_id=h.system.workspace_id,
                conversation_id=h.system.conversation_id,
                generation_id=result.run.id,
                model=h.settings.llm_model_id,
                status="completed",
                through_sequence=2,
                content="문서에서 파생된 요약",
                input_tokens=10,
                output_tokens=5,
                usage_basis="provider",
                memory_dependencies=result.run.memory_dependencies,
            )
        )
        await session.commit()
    queued = await h.submit(content="이전 내용을 이어서 설명해줘")
    await remove(h, doc["id"])
    async with h.database.session() as session:
        assert not await check_dependencies(session, result.run.memory_dependencies)
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
        assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
        assert not list(h.settings.file_storage_path.rglob(doc["version_id"]))
        summary = await session.scalar(select(ConversationCompaction))
        assert summary.content is None and summary.status == "failed"
        pending = await session.get(GenerationRun, UUID(queued["generation_id"]))
        assert pending.cancel_requested and pending.request_messages == []
        conversation = await Repository(session, h.system.user_id).get_conversation_by_id(
            h.system.conversation_id
        )
        turns = await list_context_turns(session, conversation)
        assert all(not turn.assistant_content for turn in turns)


async def test_retry_limit_and_recovery(files, monkeypatch):
    h = files
    doc = await upload(h)

    async def fail(*args, **kwargs):
        raise FileProcessingError("embedding_model_unavailable", retryable=True)

    monkeypatch.setattr(h.worker.files.embeddings, "encode", fail)
    for attempt in range(1, 4):
        assert await h.worker.files.process_next()
        async with h.database.session() as session:
            service = FileService(session, h.system.user_id, h.settings)
            row = (await service.list(h.system.conversation_id))["items"][0]
            assert row["version_id"] == doc["version_id"]
            assert row["attempts"] == attempt and row["status"] == "failed"
            assert row["can_retry"] is (attempt < 3)
            if attempt < 3:
                retried = await service.retry(h.system.conversation_id, UUID(doc["id"]))
                assert retried["version_id"] == doc["version_id"]
                await session.commit()
            else:
                assert row["dead_letter"]
                with pytest.raises(Conflict):
                    await service.retry(h.system.conversation_id, UUID(doc["id"]))
    assert not await h.worker.files.process_next()


async def test_delete_during_indexing_never_resurrects(files, monkeypatch):
    h = files
    doc = await upload(h)
    started, release = asyncio.Event(), asyncio.Event()
    encode = h.worker.files.embeddings.encode

    async def wait(*args, **kwargs):
        started.set()
        await release.wait()
        return await encode(*args, **kwargs)

    monkeypatch.setattr(h.worker.files.embeddings, "encode", wait)
    task = asyncio.create_task(h.worker.files.process_next())
    await asyncio.wait_for(started.wait(), 5)
    await remove(h, doc["id"])
    release.set()
    await task
    async with h.database.session() as session:
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
        assert (await session.get(Document, UUID(doc["id"]))).deleted_at is not None
