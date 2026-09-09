"""공급자의 JSON 이스케이프가 모델 요청과 PostgreSQL에 잘못된 Unicode를 넘기지 않는다."""

import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.tools.web_search.adapters.brave import BraveSearchProvider
from backend.app.tools.web_search.adapters.tavily import TavilySearchProvider
from backend.app.tools.web_search.provider import SearchProviderError, SearchResult


def search_provider(provider_type, results):
    payload = (
        {"results": results}
        if provider_type is TavilySearchProvider
        else {"type": "search", "web": {"results": results}}
    )

    def handler(request):
        # 실제 공급자는 UTF-8 본문 안에 JSON 이스케이프 형태로 잘못된 문자를 보낼 수 있다.
        return httpx.Response(
            200,
            content=json.dumps(payload, ensure_ascii=True).encode("ascii"),
            headers={"Content-Type": "application/json"},
        )

    return provider_type(
        api_key=SecretStr("synthetic-unicode-key"), transport=httpx.MockTransport(handler)
    )


@pytest.mark.parametrize("provider_type", [TavilySearchProvider, BraveSearchProvider])
@pytest.mark.parametrize("field", ["title", "snippet"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
async def test_unpaired_surrogate_text_is_a_sanitized_provider_error(
    provider_type, field, surrogate
):
    content_field = "content" if provider_type is TavilySearchProvider else "description"
    row = {"title": "공식 문서", "url": "https://example.com/", content_field: "정상 설명"}
    row["title" if field == "title" else content_field] = f"비정상{surrogate}설명"
    provider = search_provider(provider_type, [row])
    with pytest.raises(SearchProviderError) as raised:
        await provider.search("합성 검색")
    assert raised.value.code == "invalid_response"
    assert surrogate not in str(raised.value)


@pytest.mark.parametrize("provider_type", [TavilySearchProvider, BraveSearchProvider])
async def test_invalid_unicode_url_is_omitted_and_valid_korean_emoji_survive(provider_type):
    content_field = "content" if provider_type is TavilySearchProvider else "description"
    rows = [
        {"title": "잘못된 경로", "url": "https://example.com/\ud800", content_field: "설명"},
        {"title": "잘못된 쿼리", "url": "https://example.com/?q=\udfff", content_field: "설명"},
        {
            "title": "한글 문서 🧑‍💻",
            "url": "https://example.com/문서?q=😀",
            content_field: "정상 한글과 이모지 🧑‍💻 😀",
        },
    ]
    result = await search_provider(provider_type, rows).search("합성 검색")
    assert result.results == (
        SearchResult(
            "한글 문서 🧑‍💻", "https://example.com/문서?q=😀", "정상 한글과 이모지 🧑‍💻 😀"
        ),
    )
    for value in (result.results[0].title, result.results[0].url, result.results[0].snippet):
        assert value.encode("utf-8").decode("utf-8") == value
