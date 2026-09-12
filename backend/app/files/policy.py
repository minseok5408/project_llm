"""원본과 압축 해제 결과에 동일하게 적용하는 유한한 수집 정책."""

import re
import unicodedata
from pathlib import PurePath

from backend.app.repositories import InvalidInput

MAX_BYTES = 10 * 1024 * 1024
MAX_WORKSPACE_BYTES = 100 * 1024 * 1024
MAX_DOCUMENTS = 12
MAX_PAGES = 100
MAX_TEXT_CHARS = 150_000
MAX_CHUNKS = 256
PARSER_VERSION = "text-pdf-v1:600:80:500"
EMBEDDING_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_VERSION = f"multilingual-e5-small:{EMBEDDING_REVISION}:mean-v1"
MEDIA = {
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
    ".json": {"application/json", "text/plain"},
}


def validate_filename(value: str, media_type: str) -> tuple[str, str]:
    filename = unicodedata.normalize("NFC", value).strip()
    if (
        not filename
        or len(filename) > 180
        or filename in (".", "..")
        or any(char in filename for char in ("/", "\\", ":"))
        or any(unicodedata.category(char).startswith("C") for char in filename)
    ):
        raise InvalidInput("파일 이름이 올바르지 않습니다.")
    extension = PurePath(filename).suffix.lower()
    if extension not in MEDIA:
        raise InvalidInput("PDF, TXT, Markdown, CSV, JSON 파일만 첨부할 수 있습니다.")
    media_type = media_type.partition(";")[0].strip().lower()
    if media_type not in MEDIA[extension] | {"application/octet-stream"}:
        raise InvalidInput("파일 확장자와 콘텐츠 유형이 일치하지 않습니다.")
    return filename, extension


def validate_magic(content: bytes, extension: str) -> None:
    if not content or len(content) > MAX_BYTES:
        raise InvalidInput("파일은 비어 있지 않은 10MB 이하 파일이어야 합니다.")
    if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in content:
        raise InvalidInput("검사에서 차단한 파일입니다.")
    if extension == ".pdf":
        if not re.match(rb"%PDF-1\.[0-7]|%PDF-2\.0", content[:8]):
            raise InvalidInput("PDF 헤더가 올바르지 않습니다.")
    else:
        try:
            value = content.decode("utf-8-sig")
        except UnicodeError:
            raise InvalidInput("텍스트 파일은 UTF-8로 저장해 주세요.") from None
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise InvalidInput("바이너리 데이터가 포함된 텍스트 파일입니다.")
