"""검색 공급자와 무관한 출처 정리·응답 크기 검증을 한곳에서 적용한다."""

import ipaddress
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.app.tools.web_search.provider import SearchProviderError

MAX_RESPONSE_BYTES = 1_048_576
MAX_URL_CHARS = 2_048
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 2_000
_LOCAL_SUFFIXES = (
    ".localhost",
    ".localdomain",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".intranet",
    ".test",
    ".invalid",
    ".example",
    ".onion",
)


def public_source_url(value: object) -> str | None:
    """표시할 공개 HTTP 주소를 검증하며 DNS 조회나 결과 주소 요청은 하지 않는다."""
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        return None
    value = value.strip()
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return None
    if any(character in value for character in ("\\", "<", ">", '"', "`")):
        return None
    try:
        # JSON 이스케이프로 들어온 단독 surrogate는 HTTP·DB에 전달할 수 없다.
        value.encode("utf-8")
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"}:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        hostname = parts.hostname
        port = parts.port
        if not hostname or "%" in hostname or port not in (None, 80, 443):
            return None
        hostname = hostname.rstrip(".").lower()
        if not hostname:
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            hostname = hostname.encode("idna").decode("ascii")
            labels = hostname.split(".")
            if (
                len(labels) < 2
                or len(hostname) > 253
                or labels[-1].isdigit()
                or hostname.endswith(_LOCAL_SUFFIXES)
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
            ):
                return None
        else:
            if not address.is_global or address.is_multicast:
                return None
            if isinstance(address, ipaddress.IPv6Address):
                if address.ipv4_mapped and not address.ipv4_mapped.is_global:
                    return None
                hostname = f"[{address.compressed}]"
            else:
                hostname = str(address)
        scheme = parts.scheme.lower()
        default_port = 80 if scheme == "http" else 443
        netloc = hostname if port in (None, default_port) else f"{hostname}:{port}"
        return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    except (UnicodeError, ValueError):
        return None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str, limit: int) -> str:
    """스크립트·스타일·HTML 장식을 제거하고 화면에 표시할 글자 수를 제한한다."""
    parser = _PlainText()
    parser.feed(value[:32_000])
    parser.close()
    text = unescape("".join(parser.parts))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    result = " ".join(text.split())[:limit].strip()
    try:
        result.encode("utf-8")
    except UnicodeError:
        raise SearchProviderError("invalid_response") from None
    return result


async def read_search_json(response: httpx.Response) -> object:
    """본문을 무제한 누적하지 않고 JSON 형식과 전송·해제 크기를 제한한다."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise SearchProviderError("invalid_response")
    # 요청 시 identity를 지정하므로 압축 응답은 해제 전에 거부한다.
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise SearchProviderError("invalid_response")
    length = response.headers.get("content-length")
    if length is not None:
        if not length.isdigit() or int(length) > MAX_RESPONSE_BYTES:
            raise SearchProviderError("invalid_response")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise SearchProviderError("invalid_response")
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise SearchProviderError("invalid_response") from None
