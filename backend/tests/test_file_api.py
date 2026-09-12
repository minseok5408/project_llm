"""파일 API의 원본/근거 권한과 크기·CSRF·삭제 응답을 검증한다."""

from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.app.models import Chunk, Document
from backend.app.repositories import Repository
from backend.app.runtime.worker import GenerationWorker
from backend.tests.test_file_rag import Embeddings
from backend.tests.test_generation_api import (
    generation_client,  # noqa: F401
    login_new_account,
    new_conversation,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def setup(client, app, tmp_path):
    app.state.settings.file_storage_path = tmp_path / "uploads"
    account = await login_new_account(client)
    conversation = await new_conversation(client, account)
    return f"/api/v1/conversations/{conversation['id']}/files"


async def upload(client, path, data=b"Annual leave is 15 days."):
    return await client.post(
        path,
        content=data,
        headers={
            "Content-Type": "text/plain",
            "X-File-Name": quote("정책.txt"),
        },
    )


async def test_upload_index_download_excerpt_and_delete(generation_client, tmp_path):  # noqa: F811
    client, app, _ = generation_client
    path = await setup(client, app, tmp_path)
    response = await upload(client, path)
    assert response.status_code == 201, response.text
    file = response.json()
    document = f"{path}/{file['id']}"
    assert file["status"] == "uploaded"
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get(document + "/download")).status_code == 409
    worker = GenerationWorker(app.state.generations)
    worker.files.embeddings = Embeddings()
    assert await worker.files.process_next()
    listed = (await client.get(path)).json()
    assert listed["items"][0]["status"] == "ready"
    download = await client.get(document + "/download")
    assert download.content == b"Annual leave is 15 days."
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8''" in download.headers["content-disposition"]
    async with app.state.database.session() as session:
        chunk = await session.scalar(
            select(Chunk).where(Chunk.version_id == UUID(file["version_id"]))
        )
        chunk_id = chunk.id
    excerpt = await client.get(f"{document}/chunks/{chunk_id}")
    assert excerpt.status_code == 200 and excerpt.json()["content"] == download.text
    assert (await client.get(f"{document}/chunks/{uuid4()}")).status_code == 404
    async with app.state.database.session() as session:
        source = await session.get(Document, UUID(file["id"]))
        another = await Repository(session, source.uploaded_by).create_conversation(
            source.workspace_id, model=app.state.settings.llm_model_id
        )
        another_path = f"/api/v1/conversations/{another.id}/files"
        await session.commit()
    wrong_document = f"{another_path}/{file['id']}"
    assert (await client.get(another_path)).json()["items"] == []
    assert (await client.get(wrong_document + "/download")).status_code == 404
    assert (await client.get(f"{wrong_document}/chunks/{chunk_id}")).status_code == 404
    assert (await client.request("DELETE", wrong_document, json={})).status_code == 404
    assert (await client.post(wrong_document + "/retry", json={})).status_code == 404
    messages_path = path.removesuffix("/files") + "/messages"
    accepted = await client.post(
        messages_path,
        json={"content": "휴가 정책을 알려줘", "options": {"max_tokens": 64}},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert accepted.status_code == 202, accepted.text
    await worker.execute(await worker.claim())
    messages = (await client.get(messages_path)).json()["items"]
    sources = messages[-1]["file_sources"]
    assert sources[0]["available"] is True
    deleted = await client.request("DELETE", document, json={})
    assert deleted.status_code == 204
    assert (await client.get(document + "/download")).status_code == 404
    assert (await client.get(f"{document}/chunks/{chunk_id}")).status_code == 404
    assert (await client.get(path)).json()["items"] == []
    messages = (await client.get(messages_path)).json()["items"]
    assert messages[-1]["file_sources"][0]["available"] is False
    assert not list(tmp_path.rglob(file["version_id"]))


async def test_cross_workspace_file_requests_are_not_found(generation_client, tmp_path):  # noqa: F811
    client, app, _ = generation_client
    path = await setup(client, app, tmp_path)
    file = (await upload(client, path)).json()
    document = f"{path}/{file['id']}"
    await login_new_account(client)
    for target in (path, document + "/download", document + f"/chunks/{uuid4()}"):
        assert (await client.get(target)).status_code == 404
    assert (await upload(client, path)).status_code == 404
    assert (await client.request("DELETE", document, json={})).status_code == 404
    assert (await client.post(document + "/retry", json={})).status_code == 404


async def test_upload_validation_csrf_and_feature_switch(generation_client, tmp_path):  # noqa: F811
    client, app, _ = generation_client
    path = await setup(client, app, tmp_path)
    for headers, data, status in [
        ({"X-File-Name": "../outside.txt", "Content-Type": "text/plain"}, b"x", 422),
        ({"X-File-Name": "invalid.pdf", "Content-Type": "application/pdf"}, b"x", 422),
        ({"X-File-Name": "invalid.txt", "Content-Type": "application/pdf"}, b"x", 422),
        (
            {"X-File-Name": "invalid.txt", "Content-Type": "text/plain", "X-CSRF-Token": "wrong"},
            b"x",
            403,
        ),
        (
            {
                "X-File-Name": "invalid.txt",
                "Content-Type": "text/plain",
                "Content-Length": "10485761",
            },
            b"x",
            413,
        ),
        ({"X-File-Name": "empty.txt", "Content-Type": "text/plain"}, b"", 422),
    ]:
        response = await client.post(path, content=data, headers=headers)
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
    assert (await client.get(path)).json()["items"] == []
    file = (await upload(client, path)).json()
    app.state.settings.file_rag_enabled = False
    assert (await client.get(path)).json()["enabled"] is False
    assert (await upload(client, path)).status_code == 409
    assert (await client.request("DELETE", f"{path}/{file['id']}", json={})).status_code == 204
