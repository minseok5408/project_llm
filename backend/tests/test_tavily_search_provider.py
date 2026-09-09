"""실제 키나 외부 요청 없이 Tavily의 인증·요약·제한·중단 계약을 검사한다."""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from backend.app.tools.web_search.adapters.common import MAX_RESPONSE_BYTES
from backend.app.tools.web_search.adapters.tavily import (
    CHECK_URL,
    SEARCH_URL,
    TavilySearchProvider,
    prepare_search_query,
)
from backend.app.tools.web_search.provider import SearchProviderError, SearchResult


def provider(handler, **overrides) -> TavilySearchProvider:
    return TavilySearchProvider(
        api_key=SecretStr("synthetic-tavily-key"),
        transport=httpx.MockTransport(handler),
        **overrides,
    )


async def test_tavily_missing_key_never_connects() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    for key in (None, SecretStr(""), SecretStr("  ")):
        search = TavilySearchProvider(api_key=key, transport=httpx.MockTransport(handler))
        assert search.name == "tavily" and not search.configured
        assert await search.check() is False
        with pytest.raises(SearchProviderError) as raised:
            await search.search("합성 질문")
        assert raised.value.code == "not_configured"
    assert requests == []


async def test_tavily_post_uses_bearer_and_only_returns_clean_bounded_snippets(caplog) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "answer": "외부 답변은 사용하지 않음",
                "images": ["https://example.com/image.jpg"],
                "results": [
                    {
                        "title": " <b>공식</b> 문서 ",
                        "url": "https://EXAMPLE.com:443/docs#part",
                        "content": "<style>숨긴 스타일</style>첫째 &amp; 둘째\n 설명",
                        "raw_content": "본문 전체는 사용하지 않음",
                        "favicon": "http://127.0.0.1/favicon.png",
                    },
                    {"title": "중복", "url": "https://example.com/docs", "content": "중복"},
                    {"title": "사설", "url": "http://192.168.1.5/secrets", "content": "비공개"},
                    {"title": "메타데이터", "url": "http://169.254.169.254/", "content": "비공개"},
                    {"title": "스크립트", "url": "javascript:alert(1)", "content": "비공개"},
                ],
            },
        )

    caplog.set_level(logging.INFO, logger="httpx")
    response = await provider(handler).search("  합성질문-7145\n 검색 ")
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and str(request.url) == SEARCH_URL
    assert request.headers["authorization"] == "Bearer synthetic-tavily-key"
    assert request.headers["accept-encoding"] == "identity"
    assert "x-subscription-token" not in request.headers
    assert json.loads(request.content) == {
        "query": "합성질문-7145 검색",
        "search_depth": "basic",
        "auto_parameters": False,
        "topic": "general",
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_image_descriptions": False,
        "include_favicon": False,
    }
    assert response.results == (
        SearchResult("공식 문서", "https://example.com/docs", "첫째 & 둘째 설명"),
    )
    assert response.checked_at.utcoffset() == timedelta(0)
    assert "합성질문-7145" not in caplog.text
    assert "synthetic-tavily-key" not in caplog.text


@pytest.mark.parametrize("status,reachable", [(200, True), (302, True), (401, True), (503, False)])
async def test_tavily_check_uses_uncredentialed_head_without_redirect(
    status: int, reachable: bool
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"})

    assert await provider(handler).check() is reachable
    assert len(requests) == 1
    assert requests[0].method == "HEAD" and str(requests[0].url) == CHECK_URL
    assert "authorization" not in requests[0].headers
    assert "x-subscription-token" not in requests[0].headers
    assert requests[0].content == b""


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (432, "rate_limited"),
        (433, "rate_limited"),
        (500, "unavailable"),
        (503, "unavailable"),
        (400, "invalid_response"),
        (302, "invalid_response"),
    ],
)
async def test_tavily_errors_hide_response_and_never_forward_credentials(
    status: int, code: str
) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="synthetic-tavily-key 합성질문-7145 공급자 비밀 오류",
            headers={"Location": "http://127.0.0.1/private"},
        )

    with pytest.raises(SearchProviderError) as raised:
        await provider(handler).search("합성질문-7145")
    assert raised.value.code == code
    assert len(requests) == 1
    assert "synthetic" not in str(raised.value)
    assert "합성질문" not in str(raised.value)
    assert "비밀 오류" not in str(raised.value)


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"results": None}, {"results": {}}, {"results": "잘못된 형태"}]
)
async def test_tavily_missing_or_invalid_results_are_rejected(payload: object) -> None:
    with pytest.raises(SearchProviderError) as raised:
        await provider(lambda request: httpx.Response(200, json=payload)).search("검색")
    assert raised.value.code == "invalid_response"


async def test_tavily_empty_results_are_normal_and_malformed_individual_items_are_skipped() -> None:
    empty = await provider(lambda request: httpx.Response(200, json={"results": []})).search("검색")
    assert empty.results == ()
    response = await provider(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    None,
                    {"title": 3, "url": "https://example.com/"},
                    {"title": "제목", "url": "https://example.com/", "content": {}},
                    {"title": "<b> </b>", "url": "https://example.com/"},
                    {"title": "제목", "url": "https://example.com/", "content": None},
                ]
            },
        )
    ).search("검색")
    assert response.results == (SearchResult("제목", "https://example.com/", ""),)


async def test_tavily_limits_results_and_uses_project_query_limit_without_brave_restrictions() -> (
    None
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "제목" * 500,
                        "url": f"https://example.com/{index}",
                        "content": "내용" * 2_000,
                    }
                    for index in range(4)
                ]
            },
        )

    search = provider(handler, max_query_chars=900, max_results=2)
    response = await search.search("a " * 400)
    assert len(response.results) == 2
    assert len(response.results[0].title) == 300
    assert len(response.results[0].snippet) == 2_000
    # 짧은 단어 400개도 프로젝트의 900자 이내이면 단어 수로 잘리지 않는다.
    assert len(json.loads(requests[0].content)["query"].split()) == 400
    assert len(prepare_search_query("가" * 1_000, max_chars=900)) == 900
    assert len(prepare_search_query("가" * 1_000)) == 500
    with pytest.raises(SearchProviderError) as raised:
        await search.search("  \n ")
    assert raised.value.code == "invalid_query"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>오류</html>"),
        httpx.Response(200, content=b"{", headers={"Content-Type": "application/json"}),
        httpx.Response(
            200,
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(MAX_RESPONSE_BYTES + 1),
            },
        ),
        httpx.Response(
            200,
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "invalid"},
        ),
    ],
)
async def test_tavily_malformed_and_oversized_response_is_rejected(response) -> None:
    with pytest.raises(SearchProviderError) as raised:
        await provider(lambda request: response).search("검색")
    assert raised.value.code == "invalid_response"


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, *, block: bool = False, oversized: bool = False):
        self.block = block
        self.oversized = oversized
        self.started = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        if self.oversized:
            for _ in range(17):
                yield b"x" * 65_536
        else:
            yield b'{"results":[]}'

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("failure", ["oversized", "compressed"])
async def test_tavily_stream_limits_and_encoding_rejection_close_connection(failure: str) -> None:
    stream = TrackingStream(oversized=failure == "oversized")
    headers = {"Content-Type": "application/json"}
    if failure == "compressed":
        headers["Content-Encoding"] = "gzip"
    search = provider(lambda request: httpx.Response(200, stream=stream, headers=headers))
    with pytest.raises(SearchProviderError) as raised:
        await search.search("검색")
    assert raised.value.code == "invalid_response"
    assert stream.closed
    if failure == "compressed":
        assert not stream.started.is_set()


async def test_tavily_cancel_closes_stream_and_propagates_cancellation() -> None:
    stream = TrackingStream(block=True)
    search = provider(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        )
    )
    task = asyncio.create_task(search.search("검색"))
    await asyncio.wait_for(stream.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_tavily_search_and_reachability_have_total_time_limits() -> None:
    stream = TrackingStream(block=True)
    search = provider(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        ),
        timeout_seconds=0.01,
    )
    with pytest.raises(SearchProviderError) as raised:
        await search.search("검색")
    assert raised.value.code == "timeout" and stream.closed

    async def blocked(request):
        await asyncio.Event().wait()

    assert await provider(blocked, check_timeout_seconds=0.01).check() is False


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_tavily_transport_errors_never_expose_query_or_key(error) -> None:
    def handler(request):
        raise error("합성질문-7145 synthetic-tavily-key", request=request)

    search = provider(handler)
    assert await search.check() is False
    with pytest.raises(SearchProviderError) as raised:
        await search.search("합성질문-7145")
    assert raised.value.code == ("timeout" if error is httpx.ReadTimeout else "unavailable")
    assert "synthetic" not in str(raised.value)
    assert "합성질문" not in str(raised.value)
    assert raised.value.__suppress_context__


async def test_tavily_client_disables_environment_proxy_and_redirects(monkeypatch) -> None:
    client_class = httpx.AsyncClient
    configurations = []

    def client_factory(**kwargs):
        configurations.append(kwargs)
        return client_class(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    search = provider(lambda request: httpx.Response(200, json={"results": []}))
    await search.check()
    await search.search("검색")
    assert len(configurations) == 2
    assert all(configuration["trust_env"] is False for configuration in configurations)
    assert all(configuration["follow_redirects"] is False for configuration in configurations)
