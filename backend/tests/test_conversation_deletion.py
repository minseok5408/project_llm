"""대화와 파일 삭제의 서비스 분리 뒤에도 실패 롤백과 원문 보존을 검증한다."""

from uuid import UUID

import pytest
from sqlalchemy import func, select

from backend.app.models import Chunk, Conversation, Document, DocumentVersion, Message
from backend.app.repositories import Repository
from backend.app.runtime.worker import GenerationWorker
from backend.app.services import conversations as deletion_module
from backend.app.services.conversations import ConversationService
from backend.tests.test_file_api import setup, upload
from backend.tests.test_file_rag import Embeddings
from backend.tests.test_generation_api import generation_client as generation_client

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_conversation_and_file_deletion_roll_back_together_then_preserve_messages(
    generation_client, tmp_path, monkeypatch
):
    client, app, _ = generation_client
    path = await setup(client, app, tmp_path)
    uploaded = await upload(client, path)
    assert uploaded.status_code == 201
    file = uploaded.json()
    conversation_path = path.removesuffix("/files")
    conversation_id = UUID(conversation_path.rsplit("/", 1)[-1])
    worker = GenerationWorker(app.state.generations)
    worker.files.embeddings = Embeddings()
    assert await worker.files.process_next()
    async with app.state.database.session() as session:
        document = await session.get(Document, UUID(file["id"]))
        actor_id, workspace_id = document.uploaded_by, document.workspace_id
        await Repository(session, actor_id).append_message(
            workspace_id, conversation_id, role="user", content="삭제 뒤에도 보존할 원문"
        )
        await session.commit()

    original = deletion_module.mark_conversation_files_deleted

    async def fail_after_file_changes(*args):
        await original(*args)
        raise RuntimeError("삭제 조합 중 실패")

    with monkeypatch.context() as patch:
        patch.setattr(deletion_module, "mark_conversation_files_deleted", fail_after_file_changes)
        with pytest.raises(RuntimeError, match="삭제 조합 중 실패"):
            async with app.state.database.session() as session:
                await ConversationService(session, actor_id).soft_delete_conversation(
                    workspace_id, conversation_id
                )
    async with app.state.database.session() as session:
        assert (await session.get(Conversation, conversation_id)).deleted_at is None
        assert (await session.get(Document, UUID(file["id"]))).deleted_at is None
        assert await session.get(DocumentVersion, UUID(file["version_id"])) is not None
        assert await session.scalar(select(func.count()).select_from(Chunk)) > 0
    assert (await client.get(f"{path}/{file['id']}/download")).status_code == 200

    deleted = await client.request("DELETE", conversation_path, json={})
    assert deleted.status_code == 204
    assert (await client.get(conversation_path)).status_code == 404
    assert (await client.get(f"{path}/{file['id']}/download")).status_code == 404
    assert not list(tmp_path.rglob(file["version_id"]))
    async with app.state.database.session() as session:
        assert (await session.get(Conversation, conversation_id)).deleted_at is not None
        document = await session.get(Document, UUID(file["id"]))
        assert document.deleted_at is not None
        assert document.filename == "삭제된 파일"
        assert await session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
        assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
        assert (await session.scalar(select(Message))).content == "삭제 뒤에도 보존할 원문"
