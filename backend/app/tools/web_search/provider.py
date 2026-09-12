"""검색 공급자를 교체해도 요청 정책과 저장 형식은 유지한다."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from backend.app.config import Settings


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    results: tuple[SearchResult, ...]
    checked_at: datetime


class SearchProviderError(Exception):
    """외부 응답·검색어·인증 정보를 포함하지 않는 정해진 오류만 전달한다."""

    MESSAGES = {
        "not_configured": "웹검색 공급자 설정이 필요합니다.",
        "invalid_query": "검색할 내용이 없습니다.",
        "timeout": "웹검색 응답 시간이 초과되었습니다.",
        "unavailable": "웹검색 서비스에 연결할 수 없습니다.",
        "rate_limited": "웹검색 서비스의 요청 한도에 도달했습니다.",
        "authentication_failed": "웹검색 서비스 인증 설정을 확인해야 합니다.",
        "invalid_response": "웹검색 서비스의 응답을 확인할 수 없습니다.",
    }

    def __init__(self, code: str):
        self.code = code if code in self.MESSAGES else "unavailable"
        super().__init__(self.MESSAGES[self.code])


class WebSearchProvider(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def check(self) -> bool:
        """검색 요청 없이 공급자에 도달할 수 있는지만 확인한다."""
        ...

    async def search(self, query: str) -> SearchResponse: ...


class DisabledSearchProvider:
    name = "disabled"
    configured = False

    async def check(self) -> bool:
        return False

    async def search(self, query: str) -> SearchResponse:
        raise SearchProviderError("not_configured")


def build_search_provider(
    settings: "Settings", *, transport: httpx.AsyncBaseTransport | None = None
) -> WebSearchProvider:
    """설정만 읽어 구성하며 생성 시에는 외부 통신을 하지 않는다."""
    if settings.web_search_provider == "disabled":
        return DisabledSearchProvider()

    if settings.web_search_provider == "brave":
        from backend.app.tools.web_search.adapters.brave import BraveSearchProvider

        provider = BraveSearchProvider
    else:
        from backend.app.tools.web_search.adapters.tavily import TavilySearchProvider

        provider = TavilySearchProvider

    return provider(
        api_key=settings.web_search_api_key,
        timeout_seconds=settings.web_search_timeout_seconds,
        check_timeout_seconds=settings.web_search_check_timeout_seconds,
        max_results=settings.web_search_max_results,
        max_query_chars=settings.web_search_max_query_chars,
        transport=transport,
    )
