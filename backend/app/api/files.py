"""인증된 대화의 파일만 제한된 원본 업로드·다운로드·근거 조회를 허용한다."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select

from backend.app.api.auth import CurrentAuth, WriteAuth
from backend.app.api.conversations import (
    EmptyPayload,
    data_session,
    parse_id,
    private_json,
    read_json_payload,
    read_query,
)
from backend.app.files.policy import MAX_BYTES, validate_filename
from backend.app.files.service import FileService
from backend.app.models import Chunk
from backend.app.repositories import Conflict, InvalidInput

router = APIRouter(prefix="/api/v1/conversations/{conversation_id}/files", tags=["files"])


@asynccontextmanager
async def file_service(request, auth):
    async with data_session(request) as session:
        try:
            yield FileService(session, auth.user.id, request.app.state.settings)
        except InvalidInput as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except Conflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from None


@router.get("")
async def list_files(conversation_id: str, request: Request, auth: CurrentAuth):
    read_query(request, EmptyPayload)
    async with file_service(request, auth) as service:
        return private_json(await service.list(parse_id(conversation_id)))


@router.post("")
async def upload_file(conversation_id: str, request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    conversation_id = parse_id(conversation_id)
    try:
        filename = unquote(request.headers.get("x-file-name", ""), errors="strict")
    except UnicodeError:
        raise HTTPException(
            status_code=422, detail="파일 이름의 인코딩이 올바르지 않습니다."
        ) from None
    media_type = request.headers.get("content-type", "")
    async with file_service(request, auth) as service:
        await service.conversation(conversation_id, write=True)
        validate_filename(filename, media_type)
        declared = request.headers.get("content-length")
        if declared and (not declared.isdigit() or int(declared) > MAX_BYTES):
            raise HTTPException(status_code=413, detail="파일은 10MB까지 첨부할 수 있습니다.")
        data = bytearray()
        # 길이 헤더를 신뢰하지 않고 스트림 자체에도 상한·수신 시간 제한을 둔다.
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(
                            status_code=413, detail="파일은 10MB까지 첨부할 수 있습니다."
                        )
                    data.extend(chunk)
        except TimeoutError:
            raise HTTPException(
                status_code=408, detail="파일 업로드 시간이 초과되었습니다."
            ) from None
        result = await service.upload(conversation_id, filename, media_type, bytes(data))
        await service.session.commit()
        return private_json(result, status_code=201)


@router.post("/{document_id}/retry")
async def retry_file(conversation_id: str, document_id: str, request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    await read_json_payload(request, EmptyPayload)
    async with file_service(request, auth) as service:
        result = await service.retry(parse_id(conversation_id), parse_id(document_id))
        await service.session.commit()
        return private_json(result)


@router.delete("/{document_id}")
async def delete_file(conversation_id: str, document_id: str, request: Request, auth: WriteAuth):
    read_query(request, EmptyPayload)
    await read_json_payload(request, EmptyPayload)
    async with file_service(request, auth) as service:
        await service.delete(parse_id(conversation_id), parse_id(document_id))
        await service.session.commit()
        await service.cleanup()
        await service.session.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/{document_id}/download")
async def download_file(
    conversation_id: str, document_id: str, request: Request, auth: CurrentAuth
):
    read_query(request, EmptyPayload)
    async with file_service(request, auth) as service:
        document, version = await service.get(parse_id(conversation_id), parse_id(document_id))
        if version.status != "ready":
            raise Conflict("검사를 마친 파일만 다운로드할 수 있습니다.")
        data = await asyncio.to_thread(
            service.storage.read, document.workspace_id, document.id, version.id
        )
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(document.filename, safe=""),
            },
        )


@router.get("/{document_id}/chunks/{chunk_id}")
async def file_excerpt(
    conversation_id: str, document_id: str, chunk_id: str, request: Request, auth: CurrentAuth
):
    read_query(request, EmptyPayload)
    async with file_service(request, auth) as service:
        document, version = await service.get(parse_id(conversation_id), parse_id(document_id))
        chunk = await service.session.scalar(
            select(Chunk).where(
                Chunk.id == parse_id(chunk_id),
                Chunk.version_id == version.id,
            )
        )
        if chunk is None or version.status != "ready":
            raise HTTPException(status_code=404, detail="파일 근거를 찾을 수 없습니다.")
        return private_json(
            {
                "filename": document.filename,
                "page": chunk.page,
                "chunk": chunk.ordinal,
                "start": chunk.start_char,
                "end": chunk.end_char,
                "content": chunk.content,
            }
        )
