"""첨부 파일의 권한·한도·멱등 업로드와 원본 삭제를 한 경계에서 관리한다."""

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update

from backend.app.files.policy import (
    MAX_BYTES,
    MAX_DOCUMENTS,
    MAX_WORKSPACE_BYTES,
    validate_filename,
    validate_magic,
)
from backend.app.files.storage import LocalFiles
from backend.app.models import (
    Chunk,
    Conversation,
    ConversationCompaction,
    Document,
    DocumentVersion,
    GenerationRun,
    User,
    Workspace,
)
from backend.app.repositories import AccessDenied, Conflict, Repository

ERRORS = {
    "embedding_model_unavailable": "로컬 검색 모델이 없습니다. 모델 설치 후 재시도해 주세요.",
    "embedding_failed": "검색 색인을 만들지 못했습니다. 다시 시도해 주세요.",
    "parser_sandbox_unavailable": "파일 파서 격리 환경을 사용할 수 없습니다.",
    "parser_timeout": "문서 처리 시간이 상한을 초과했습니다. 파일을 나눠 주세요.",
    "parser_memory_limit": "문서 처리 메모리 상한을 초과했습니다.",
    "no_text": "추출할 텍스트가 없습니다. 스캔 PDF의 OCR은 아직 지원하지 않습니다.",
    "encrypted_pdf": "암호가 걸린 PDF는 지원하지 않습니다.",
    "active_pdf": "스크립트·양식·내장 파일이 포함된 PDF는 지원하지 않습니다.",
    "page_limit": "PDF는 100페이지까지 지원합니다.",
    "text_limit": "추출 텍스트는 15만 자까지 지원합니다. 파일을 나눠 주세요.",
    "chunk_limit": "검색 조각 상한을 초과했습니다. 파일을 나눠 주세요.",
    "scan_blocked": "파일 검사에서 차단했습니다.",
    "scanner_unavailable": "파일 검사기를 사용할 수 없습니다. 설정 확인 후 재시도해 주세요.",
    "worker_interrupted": "파일 처리가 중단되었습니다. 다시 시도해 주세요.",
    "source_missing": "원본 파일을 읽을 수 없습니다. 삭제 후 다시 첨부해 주세요.",
}
RETRYABLE = {
    "embedding_model_unavailable",
    "embedding_failed",
    "worker_interrupted",
    "scanner_unavailable",
}


def payload(document, version, can_manage: bool) -> dict:
    return {
        "id": str(document.id),
        "filename": document.filename,
        "version_id": str(version.id),
        "status": version.status,
        "byte_size": version.byte_size,
        "pages": version.page_count,
        "chunks": version.chunk_count,
        "attempts": version.attempts,
        "error": (
            ERRORS.get(version.error_code, "파일 형식 또는 내용이 올바르지 않습니다.")
            if version.error_code
            else None
        ),
        "can_delete": can_manage,
        "can_retry": can_manage
        and version.status == "failed"
        and version.attempts < 3
        and version.error_code in RETRYABLE,
        "dead_letter": version.status == "failed" and version.attempts >= 3,
    }


class FileService:
    def __init__(self, session, actor_id: UUID, settings):
        self.session, self.actor_id, self.settings = session, actor_id, settings
        self.storage = LocalFiles(settings.file_storage_path)
        self.repository = Repository(session, actor_id)

    async def conversation(self, conversation_id: UUID, *, write: bool = False):
        conversation = await self.repository.get_conversation_by_id(conversation_id)
        if write and (not self.settings.file_rag_enabled or conversation.status != "active"):
            raise Conflict("현재 파일 첨부를 사용할 수 없습니다.")
        return conversation

    async def _lock(self, conversation_id: UUID):
        await self.session.scalar(select(User).where(User.id == self.actor_id).with_for_update())
        conversation = await self.conversation(conversation_id, write=True)
        await self.repository.require_membership(conversation.workspace_id, lock=True)
        await self.session.scalar(
            select(Workspace).where(Workspace.id == conversation.workspace_id).with_for_update()
        )
        return await self.session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _manager(self, document) -> bool:
        member = await self.repository.require_membership(document.workspace_id)
        return document.uploaded_by == self.actor_id or member.role in ("owner", "admin")

    async def list(self, conversation_id: UUID) -> dict:
        conversation = await self.conversation(conversation_id)
        member = await self.repository.require_membership(conversation.workspace_id)
        rows = (
            await self.session.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion)
                .where(Document.conversation_id == conversation_id, Document.deleted_at.is_(None))
                .order_by(Document.created_at, Document.id)
                .limit(MAX_DOCUMENTS)
            )
        ).all()
        return {
            "enabled": self.settings.file_rag_enabled,
            "max_bytes": MAX_BYTES,
            "limit": MAX_DOCUMENTS,
            "items": [
                payload(d, v, d.uploaded_by == self.actor_id or member.role in ("owner", "admin"))
                for d, v in rows
            ],
        }

    async def get(self, conversation_id: UUID, document_id: UUID):
        await self.conversation(conversation_id)
        row = (
            await self.session.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion)
                .where(
                    Document.id == document_id,
                    Document.conversation_id == conversation_id,
                    Document.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise AccessDenied("파일을 찾을 수 없습니다.")
        return row

    async def upload(
        self, conversation_id: UUID, filename: str, media_type: str, data: bytes
    ) -> dict:
        filename, extension = validate_filename(filename, media_type)
        validate_magic(data, extension)
        conversation = await self._lock(conversation_id)
        digest = hashlib.sha256(data).hexdigest()
        duplicate = (
            await self.session.execute(
                select(Document, DocumentVersion)
                .join(DocumentVersion)
                .where(
                    Document.conversation_id == conversation_id,
                    Document.deleted_at.is_(None),
                    DocumentVersion.sha256 == digest,
                )
            )
        ).one_or_none()
        if duplicate:
            return payload(*duplicate, await self._manager(duplicate[0]))
        count = await self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.conversation_id == conversation_id, Document.deleted_at.is_(None))
        )
        size = await self.session.scalar(
            select(func.coalesce(func.sum(DocumentVersion.byte_size), 0))
            .join(Document)
            .where(
                Document.workspace_id == conversation.workspace_id, Document.deleted_at.is_(None)
            )
        )
        workspace_count = await self.session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.workspace_id == conversation.workspace_id, Document.deleted_at.is_(None)
            )
        )
        if (
            count >= MAX_DOCUMENTS
            or workspace_count >= 100
            or size + len(data) > MAX_WORKSPACE_BYTES
        ):
            raise Conflict(
                "대화당 12개, 작업 공간당 100개·100MB까지 첨부할 수 있습니다. "
                "기존 파일을 삭제해 주세요."
            )
        document = Document(
            id=uuid4(),
            workspace_id=conversation.workspace_id,
            conversation_id=conversation_id,
            uploaded_by=self.actor_id,
            filename=filename,
        )
        version = DocumentVersion(
            id=uuid4(),
            document_id=document.id,
            version=1,
            sha256=digest,
            byte_size=len(data),
            extension=extension,
            status="uploaded",
            attempts=0,
        )
        self.session.add(document)
        await self.session.flush()
        self.session.add(version)
        await self.session.flush()
        location = (document.workspace_id, document.id, version.id)
        try:
            writing = asyncio.create_task(asyncio.to_thread(self.storage.write, *location, data))
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                await asyncio.gather(writing, return_exceptions=True)
                raise
            result = payload(document, version, True)
            await self.session.commit()
            return result
        except BaseException:
            await self.session.rollback()
            await asyncio.to_thread(self.storage.delete, *location)
            raise

    async def retry(self, conversation_id: UUID, document_id: UUID) -> dict:
        await self._lock(conversation_id)
        document, version = await self.get(conversation_id, document_id)
        result = payload(document, version, await self._manager(document))
        if not result["can_retry"]:
            raise Conflict("재시도할 수 없습니다. 파일을 삭제하고 새 파일을 첨부해 주세요.")
        version.status, version.error_code = "uploaded", None
        await self.session.flush()
        return payload(document, version, True)

    async def delete(self, conversation_id: UUID, document_id: UUID):
        # 비활성 기능·보관된 대화에서도 삭제는 가능하다.
        await self.session.scalar(select(User).where(User.id == self.actor_id).with_for_update())
        document, version = await self.get(conversation_id, document_id)
        if not await self._manager(document):
            raise AccessDenied("업로드한 사용자 또는 작업 공간 관리자만 삭제할 수 있습니다.")
        await self.session.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
        document = await self.session.scalar(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self.remove(document, version)

    async def remove(self, document, version):
        # 문서 잠금과 같은 순서로 실행 이력을 잠가 색인 완료·문맥 저장과 삭제를 직렬화한다.
        key = f"document:{document.id}"
        document.deleted_at = datetime.now(UTC)
        document.filename = "삭제된 파일"
        await self.session.execute(
            update(GenerationRun)
            .where(
                GenerationRun.conversation_id == document.conversation_id,
                GenerationRun.status.in_(("queued", "running")),
            )
            .values(cancel_requested=True, request_messages=[])
        )
        await self.session.execute(
            update(ConversationCompaction)
            .where(
                ConversationCompaction.memory_dependencies.has_key(key),
            )
            .values(status="failed", content=None, error_code="document_deleted")
        )
        await self.session.execute(delete(Chunk).where(Chunk.version_id == version.id))
        # 먼저 검색 권한을 없애고 삭제 대기 버전은 청소 작업이 원본까지 회수한다.
        await self.session.flush()

    async def cleanup(self) -> int:
        return await cleanup_deleted_files(self.session, self.settings)


async def cleanup_deleted_files(session, settings) -> int:
    storage = LocalFiles(settings.file_storage_path)
    rows = (
        await session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion)
            .where(Document.deleted_at.is_not(None))
            .limit(12)
        )
    ).all()
    for document, version in rows:
        await asyncio.to_thread(storage.delete, document.workspace_id, document.id, version.id)
        await session.delete(version)
    await session.flush()
    return len(rows)
