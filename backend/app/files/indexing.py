"""생성 실행자의 빈 슬롯에서 파일 상태를 영속화하며 격리 파싱·로컬 색인을 만든다."""

import asyncio
import hashlib
from time import monotonic

from sqlalchemy import delete, select, update

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.files.embeddings import LocalEmbeddings
from backend.app.files.parser import FileProcessingError, isolated_parse
from backend.app.files.policy import EMBEDDING_VERSION, MAX_CHUNKS, PARSER_VERSION, validate_magic
from backend.app.files.service import cleanup_deleted_files
from backend.app.files.storage import LocalFiles
from backend.app.models import Chunk, Conversation, Document, DocumentVersion, Workspace
from backend.app.repositories import InvalidInput


def chunk_pages(pages: list[dict], *, size: int = 600, overlap: int = 80) -> list[dict]:
    if not 100 <= size <= 1600 or not 0 <= overlap < size:
        raise ValueError("조각 크기와 겹침 범위가 올바르지 않습니다.")
    result = []
    for page in pages:
        content, start = page["text"], 0
        while start < len(content):
            end = min(start + size, len(content))
            if end < len(content):
                # 문장·행 경계를 선호하지만 긴 행에서도 반드시 전진한다.
                boundary = max(
                    content.rfind("\n", start + size // 2, end),
                    content.rfind(". ", start + size // 2, end),
                )
                if boundary > start:
                    end = boundary + 1
            if content[start:end].strip():
                result.append(
                    {
                        "ordinal": len(result) + 1,
                        "page": page["page"],
                        "start_char": start,
                        "end_char": end,
                        "content": content[start:end],
                    }
                )
            if end == len(content):
                break
            start = max(start + 1, end - overlap)
    if len(result) > MAX_CHUNKS:
        raise FileProcessingError("chunk_limit")
    return result


class FileIndexer:
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings
        self.last_cleanup = 0.0
        self.storage = LocalFiles(settings.file_storage_path)
        self.embeddings = LocalEmbeddings(settings.file_embedding_path)

    async def recover(self):
        async with self.database.session() as session:
            await session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.status.in_(("scanning", "parsing", "indexing")))
                .values(status="failed", error_code="worker_interrupted")
            )
            await cleanup_deleted_files(session, self.settings)
            await session.commit()

    async def _state(self, version_id, status: str, **values) -> bool:
        async with self.database.session() as session:
            document = await session.scalar(
                select(Document)
                .join(DocumentVersion)
                .where(DocumentVersion.id == version_id, Document.deleted_at.is_(None))
                .with_for_update(of=Document)
            )
            if document is None:
                return False
            await session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id == version_id)
                .values(status=status, **values)
            )
            await session.commit()
            return True

    async def scan(self, data: bytes, extension: str) -> str:
        try:
            validate_magic(data, extension)
        except InvalidInput:
            raise FileProcessingError("scan_blocked") from None
        scanner = self.settings.file_clamav_path
        if scanner is None:
            return "structural-v1"
        if not scanner.is_absolute() or not scanner.is_file():
            raise FileProcessingError("scanner_unavailable", retryable=True)
        process = await asyncio.create_subprocess_exec(
            str(scanner),
            "--no-summary",
            "--max-filesize=10M",
            "--max-scansize=20M",
            "--max-recursion=8",
            "--max-files=100",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(data), timeout=30)
            if process.returncode == 1:
                raise FileProcessingError("scan_blocked")
            if process.returncode:
                raise FileProcessingError("scanner_unavailable", retryable=True)
        except TimeoutError:
            raise FileProcessingError("scanner_unavailable", retryable=True) from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return "clamav+structural-v1"

    async def process_next(self) -> bool:
        if monotonic() - self.last_cleanup >= 5:
            async with self.database.session() as session:
                await cleanup_deleted_files(session, self.settings)
                await session.commit()
            self.last_cleanup = monotonic()
        if not self.settings.file_rag_enabled:
            return False
        async with self.database.session() as session:
            row = (
                await session.execute(
                    select(Document, DocumentVersion)
                    .join(DocumentVersion)
                    .join(Conversation, Conversation.id == Document.conversation_id)
                    .join(Workspace, Workspace.id == Document.workspace_id)
                    .where(
                        Document.deleted_at.is_(None),
                        Conversation.deleted_at.is_(None),
                        Workspace.status == "active",
                        DocumentVersion.status == "uploaded",
                        DocumentVersion.attempts < 3,
                    )
                    .order_by(DocumentVersion.created_at, DocumentVersion.id)
                    .limit(1)
                    .with_for_update(of=DocumentVersion, skip_locked=True)
                )
            ).one_or_none()
            if row is None:
                return False
            document, version = row
            version.status, version.error_code = "scanning", None
            version.attempts += 1
            location = (document.workspace_id, document.id, version.id)
            extension, digest = version.extension, version.sha256
            await session.commit()
        try:
            try:
                data = await asyncio.to_thread(self.storage.read, *location)
            except OSError:
                raise FileProcessingError("source_missing") from None
            if hashlib.sha256(data).hexdigest() != digest:
                raise FileProcessingError("source_missing")
            engine = await self.scan(data, extension)
            if not await self._state(version.id, "parsing", scan_engine=engine):
                return True
            pages = await isolated_parse(data, extension)
            chunks = chunk_pages(pages)
            if not await self._state(version.id, "indexing", parser_version=PARSER_VERSION):
                return True
            chunks = await self.embeddings.split(chunks)
            vectors = await self.embeddings.encode([chunk["content"] for chunk in chunks])
            async with self.database.session() as session:
                current = await session.scalar(
                    select(Document).where(Document.id == document.id).with_for_update()
                )
                if current is None or current.deleted_at:
                    return True
                await session.execute(delete(Chunk).where(Chunk.version_id == version.id))
                session.add_all(
                    [
                        Chunk(version_id=version.id, **chunk, embedding=vector)
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ]
                )
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.id == version.id)
                    .values(
                        status="ready",
                        embedding_version=EMBEDDING_VERSION,
                        chunk_count=len(chunks),
                        page_count=len(pages),
                        error_code=None,
                    )
                )
                await session.commit()
        except asyncio.CancelledError:
            await asyncio.shield(self._state(version.id, "failed", error_code="worker_interrupted"))
            raise
        except FileProcessingError as error:
            await self._state(version.id, "failed", error_code=error.code)
        except Exception:
            await self._state(version.id, "failed", error_code="embedding_failed")
        return True
