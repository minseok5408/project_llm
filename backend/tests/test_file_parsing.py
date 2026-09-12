"""실제 격리 파서와 원본 저장소의 경계·상한·원문 위치를 검사한다."""

import asyncio
import io
import sys
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.files.indexing import chunk_pages
from backend.app.files.parser import FileProcessingError, isolated_parse, sandbox_profile
from backend.app.files.policy import MAX_BYTES, validate_filename, validate_magic
from backend.app.files.storage import LocalFiles
from backend.app.repositories import InvalidInput


def text_pdf(text="Annual leave: 15 days", *, pages=1, active=False, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    if active:
        writer.add_js("app.alert('no');")
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(
    "filename,media",
    [
        ("../note.txt", "text/plain"),
        ("x\\note.txt", "text/plain"),
        ("x\u202etxt.pdf", "application/pdf"),
        ("x\x00.txt", "text/plain"),
        ("x.exe", "application/octet-stream"),
        ("note.txt", "application/pdf"),
        ("a" * 180 + ".txt", "text/plain"),
    ],
)
def test_filename_and_media(filename, media):
    with pytest.raises(InvalidInput):
        validate_filename(filename, media)


@pytest.mark.parametrize(
    "data,extension",
    [
        (b"", ".txt"),
        (b"x" * (MAX_BYTES + 1), ".txt"),
        (b"MZ\x00", ".txt"),
        (b"not a pdf", ".pdf"),
        (b"\xff", ".txt"),
        (b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE", ".txt"),
    ],
)
def test_magic_size_and_binary(data, extension):
    with pytest.raises(InvalidInput):
        validate_magic(data, extension)


@pytest.mark.asyncio
async def test_real_text_and_pdf_parser():
    pages = await isolated_parse("한국어 원문\n개발 문서".encode(), ".md")
    assert pages == [{"page": 1, "text": "한국어 원문\n개발 문서"}]
    pages = await isolated_parse(text_pdf(pages=2), ".pdf")
    assert [page["page"] for page in pages] == [1, 2]
    assert all("15 days" in page["text"] for page in pages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,code",
    [
        ("active", "active_pdf"),
        ("encrypted", "encrypted_pdf"),
        ("pages", "page_limit"),
        ("empty", "no_text"),
    ],
)
async def test_pdf_refuses_active_encrypted_and_oversized(kind, code):
    data = text_pdf(
        active=kind == "active",
        encrypted=kind == "encrypted",
        pages=101 if kind == "pages" else 1,
        text="" if kind == "empty" else "hello",
    )
    with pytest.raises(FileProcessingError, match=code):
        await isolated_parse(data, ".pdf")


@pytest.mark.asyncio
async def test_invalid_json_and_extraction_size():
    with pytest.raises(FileProcessingError, match="invalid_document"):
        await isolated_parse(b'{"unfinished":', ".json")
    with pytest.raises(FileProcessingError, match="text_limit"):
        await isolated_parse(b"a" * 150_001, ".txt")


@pytest.mark.asyncio
async def test_sandbox_blocks_network_service_data_and_writes(tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("synthetic secret")
    code = f"""import socket
from pathlib import Path
results = []
for operation in [
    lambda: Path({str(secret)!r}).read_text(),
    lambda: Path({str(tmp_path / "written")!r}).write_text("x"),
    lambda: socket.socket().connect(("127.0.0.1", 9))]:
    try: operation(); results.append("allowed")
    except PermissionError: results.append("blocked")
print(results)
"""
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/sandbox-exec",
        "-p",
        sandbox_profile(),
        sys.executable,
        "-I",
        "-c",
        code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"LANG": "en_US.UTF-8"},
        cwd="/private/tmp",
    )
    output, error = await process.communicate()
    assert process.returncode == 0, error
    assert output.decode().count("blocked") == 3


def test_storage_opaque_paths_symlink_and_removal(tmp_path):
    storage = LocalFiles(tmp_path / "files")
    ids = uuid4(), uuid4(), uuid4()
    storage.write(*ids, b"hello")
    assert storage.read(*ids) == b"hello"
    assert storage.path(*ids).stat().st_mode & 0o777 == 0o600
    storage.delete(*ids)
    assert not storage.path(*ids).exists()
    target = tmp_path / "outside"
    target.mkdir()
    (storage.root / str(ids[0]) / str(ids[1])).symlink_to(target)
    with pytest.raises(OSError):
        storage.write(*ids, b"secret")


def test_chunk_offsets_reconstruct_original_and_are_bounded():
    content = "문서 원문입니다. 줄 경계를 보존합니다.\n" * 100
    chunks = chunk_pages([{"page": 2, "text": content}])
    assert len(chunks) > 1
    covered = set()
    for chunk in chunks:
        assert chunk["content"] == content[chunk["start_char"] : chunk["end_char"]]
        assert chunk["page"] == 2 and len(chunk["content"]) <= 900
        covered.update(range(chunk["start_char"], chunk["end_char"]))
    assert len(covered) == len(content)
