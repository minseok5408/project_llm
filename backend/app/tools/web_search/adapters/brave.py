"""Brave 검색 API 요약만 사용하며 결과 웹페이지의 본문은 가져오지 않는다."""

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

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
CHECK_URL = "https://api.search.brave.com/"


def prepare_search_query(query: str, *, max_chars: int = 500) -> str:
    """공식 검색어 제한인 600자·75단어보다 작은 프로젝트 한도를 적용한다."""
    normalized = " ".join(query.split())[: max(1, min(max_chars, 600))].strip()
    return " ".join(normalized.split()[:75])


def _results(payload: object, limit: int) -> tuple[SearchResult, ...]:
    if not isinstance(payload, dict):
        raise SearchProviderError("invalid_response")
    web = payload.get("web")
    # 정상 검색에서도 결과가 없으면 web 필드는 생략될 수 있다.
    if web is None and payload.get("type") == "search":
        return ()
    if not isinstance(web, dict) or not isinstance(web.get("results"), list):
        raise SearchProviderError("invalid_response")
    selected: list[SearchResult] = []
    seen: set[str] = set()
    for raw in web["results"]:
        if not isinstance(raw, dict):
            continue
        url = public_source_url(raw.get("url"))
        title = raw.get("title")
        snippet = raw.get("description", "")
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


class BraveSearchProvider:
    name = "brave"

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
        # 프록시 환경변수·리다이렉트를 사용하지 않아 비밀 헤더가 다른 호스트로 가지 않는다.
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
                    # 루트 HEAD에는 API 키·검색어가 없으며 검색·키 유효성을 검사하지 않는다.
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
                    # POST 본문을 사용하여 검색어가 일반 HTTP URL 로그에 남지 않게 한다.
                    async with client.stream(
                        "POST",
                        SEARCH_URL,
                        json={
                            "q": normalized,
                            "count": self._max_results,
                            "country": "KR",
                            "search_lang": "ko",
                            "result_filter": ["web"],
                            "text_decorations": False,
                        },
                        headers={"X-Subscription-Token": self._api_key.get_secret_value()},
                    ) as response:
                        if response.status_code in (401, 403):
                            raise SearchProviderError("authentication_failed")
                        if response.status_code == 429:
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
