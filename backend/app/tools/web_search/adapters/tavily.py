"""Tavily 검색 요약을 받아 로컬 모델에 전달하며 외부 답변 생성은 요청하지 않는다."""

import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from backend.app.tools.web_search.adapters.common import (
    MAX_SNIPPET_CHARS,
    MAX_TITLE_CHARS,
    plain_text,
    public_source_url,
    read_search_json,
)
from backend.app.tools.web_search.provider import (
    SearchProviderError,
    SearchResponse,
    SearchResult,
)

SEARCH_URL = "https://api.tavily.com/search"
CHECK_URL = "https://api.tavily.com/"


def prepare_search_query(query: str, *, max_chars: int = 500) -> str:
    """검색어 공백을 정리하고 프로젝트의 글자 한도만 적용한다."""
    return " ".join(query.split())[: max(1, max_chars)].strip()


def _results(payload: object, limit: int) -> tuple[SearchResult, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SearchProviderError("invalid_response")
    selected: list[SearchResult] = []
    seen: set[str] = set()
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            continue
        url = public_source_url(raw.get("url"))
        title = raw.get("title")
        snippet = raw.get("content", "")
        if not url or url in seen or not isinstance(title, str):
            continue
        if snippet is None:
            snippet = ""
        if not isinstance(snippet, str):
            continue
        title = plain_text(title, MAX_TITLE_CHARS)
        if not title:
            continue
        seen.add(url)
        selected.append(SearchResult(title, url, plain_text(snippet, MAX_SNIPPET_CHARS)))
        if len(selected) >= limit:
            break
    return tuple(selected)


class TavilySearchProvider:
    name = "tavily"

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        timeout_seconds: float = 8,
        check_timeout_seconds: float = 2,
        max_results: int = 5,
        max_query_chars: int = 500,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._check_timeout = check_timeout_seconds
        self._max_results = max(1, min(max_results, 20))
        self._max_query_chars = max_query_chars
        self._transport = transport

    @property
    def configured(self) -> bool:
        return self._api_key is not None and bool(self._api_key.get_secret_value().strip())

    def _client(self, timeout: float) -> httpx.AsyncClient:
        # 고정 주소 외 리다이렉트와 환경 프록시로 인증 정보가 흘러가지 않게 한다.
        return httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=self._transport,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )

    async def check(self) -> bool:
        if not self.configured:
            return False
        try:
            async with asyncio.timeout(self._check_timeout):
                async with self._client(self._check_timeout) as client:
                    # 키·검색어 없는 루트 HEAD는 검색이나 키 유효성 대신 도달성만 확인한다.
                    async with client.stream("HEAD", CHECK_URL) as response:
                        return 200 <= response.status_code < 500
        except (httpx.HTTPError, TimeoutError):
            return False

    async def search(self, query: str) -> SearchResponse:
        if not self.configured:
            raise SearchProviderError("not_configured")
        normalized = prepare_search_query(query, max_chars=self._max_query_chars)
        if not normalized:
            raise SearchProviderError("invalid_query")
        assert self._api_key is not None
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client(self._timeout) as client:
                    async with client.stream(
                        "POST",
                        SEARCH_URL,
                        json={
                            "query": normalized,
                            "search_depth": "basic",
                            "auto_parameters": False,
                            "topic": "general",
                            "max_results": self._max_results,
                            "include_answer": False,
                            "include_raw_content": False,
                            "include_images": False,
                            "include_image_descriptions": False,
                            "include_favicon": False,
                        },
                        headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                    ) as response:
                        if response.status_code in (401, 403):
                            raise SearchProviderError("authentication_failed")
                        if response.status_code in (429, 432, 433):
                            raise SearchProviderError("rate_limited")
                        if response.status_code >= 500:
                            raise SearchProviderError("unavailable")
                        if response.status_code != 200:
                            raise SearchProviderError("invalid_response")
                        results = _results(await read_search_json(response), self._max_results)
                        return SearchResponse(results, datetime.now(UTC))
        except (httpx.TimeoutException, TimeoutError):
            raise SearchProviderError("timeout") from None
        except httpx.HTTPError:
            raise SearchProviderError("unavailable") from None
        except (ValueError, RecursionError):
            raise SearchProviderError("invalid_response") from None
