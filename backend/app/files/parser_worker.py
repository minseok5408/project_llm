"""서비스 모듈·비밀 환경변수 없이 격리 프로세스에서 제한된 텍스트만 추출한다."""

import io
import json
import resource
import sys


def parse(data: bytes, extension: str) -> list[dict]:
    if extension != ".pdf":
        content = data.decode("utf-8-sig")
        if extension == ".json":
            json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        pages = [{"page": 1, "text": content}]
    else:
        import pypdf
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

        limits = {}
        for field in (
            "maximum_declared_stream_length",
            "array_based_stream_maximum_output_length",
            "zlib_maximum_output_length",
            "lzw_maximum_output_length",
            "run_length_maximum_output_length",
            "image_maximum_buffer_size",
        ):
            limits[field] = 8 * 1024 * 1024
        pypdf.overwrite_configuration(
            **limits,
            page_tree_maximum_entries=2000,
            xform_maximum_invocations_per_extraction=100,
            disable_legacy_handling=True,
        )
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        if not 0 < len(reader.pages) <= 100:
            raise ValueError("page_limit")
        blocked = {
            "/JS",
            "/JavaScript",
            "/AA",
            "/OpenAction",
            "/EmbeddedFiles",
            "/EF",
            "/RichMedia",
            "/XFA",
            "/AcroForm",
        }
        seen, pending, count = set(), [reader.trailer], 0
        while pending:
            value = pending.pop()
            count += 1
            if count > 50_000:
                raise ValueError("object_limit")
            if isinstance(value, IndirectObject):
                key = (value.idnum, value.generation)
                if key in seen:
                    continue
                seen.add(key)
                value = value.get_object()
            if isinstance(value, DictionaryObject):
                if blocked.intersection(value) or value.get("/S") in (
                    "/JavaScript",
                    "/Launch",
                    "/SubmitForm",
                    "/ImportData",
                    "/GoToR",
                ):
                    raise ValueError("active_pdf")
                pending.extend(value.values())
            elif isinstance(value, ArrayObject):
                pending.extend(value)
        pages, count = [], 0
        for number, page in enumerate(reader.pages, 1):
            value = page.extract_text() or ""
            count += len(value)
            if count > 150_000:
                raise ValueError("text_limit")
            pages.append({"page": number, "text": value})
    if sum(len(page["text"]) for page in pages) > 150_000:
        raise ValueError("text_limit")
    if not any(page["text"].strip() for page in pages):
        raise ValueError("no_text")
    if any("\x00" in page["text"] for page in pages):
        raise ValueError("binary_text")
    return pages


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    # macOS의 주소 공간·데이터 제한 대신 부모 프로세스에서 RSS 상한을 감시한다.
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (48, 48))
    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    try:
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError("size_limit")
        pages = parse(data, sys.argv[1])
        print(json.dumps({"pages": pages}, ensure_ascii=False))
    except Exception as error:
        allowed = {
            "encrypted_pdf",
            "page_limit",
            "object_limit",
            "active_pdf",
            "text_limit",
            "no_text",
            "binary_text",
            "size_limit",
        }
        code = str(error) if str(error) in allowed else "invalid_document"
        print(json.dumps({"error": code}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
