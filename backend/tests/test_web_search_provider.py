"""외부 통신 없이 검색 API의 연결·취소·비밀 보호·출처 검증을 검사한다."""

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from backend.app.config import Settings
from backend.app.tools.web_search.adapters.brave import (
    CHECK_URL,
    SEARCH_URL,
    BraveSearchProvider,
    prepare_search_query,
)
from backend.app.tools.web_search.adapters.common import MAX_RESPONSE_BYTES, public_source_url
from backend.app.tools.web_search.provider import (
    SearchProviderError,
    SearchResult,
    build_search_provider,
)


def provider(handler, **overrides) -> BraveSearchProvider:
    return BraveSearchProvider(
        api_key=SecretStr("synthetic-search-key"),
        transport=httpx.MockTransport(handler),
        **overrides,
    )


async def test_missing_key_never_connects() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    for key in (None, SecretStr(""), SecretStr("  ")):
        search = BraveSearchProvider(api_key=key, transport=httpx.MockTransport(handler))
        assert search.configured is False
        assert await search.check() is False
        with pytest.raises(SearchProviderError) as raised:
            await search.search("최신 프로그래밍 소식")
        assert raised.value.code == "not_configured"
    assert requests == []


async def test_factory_does_not_connect_and_disabled_provider_stays_off() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    settings = Settings(_env_file=None, llm_backend="mock").model_copy(
        update={
            "web_search_provider": "brave",
            "web_search_api_key": SecretStr("synthetic-search-key"),
            "web_search_timeout_seconds": 8,
            "web_search_check_timeout_seconds": 2,
            "web_search_max_results": 5,
            "web_search_max_query_chars": 500,
        }
    )
    search = build_search_provider(settings, transport=httpx.MockTransport(handler))
    assert search.name == "brave" and search.configured
    assert requests == []
    disabled = build_search_provider(
        settings.model_copy(update={"web_search_provider": "disabled"}),
        transport=httpx.MockTransport(handler),
    )
    assert disabled.name == "disabled" and not disabled.configured
    assert await disabled.check() is False
    with pytest.raises(SearchProviderError):
        await disabled.search("검색")
    assert requests == []


@pytest.mark.parametrize("status,reachable", [(200, True), (301, True), (404, True), (503, False)])
async def test_check_uses_only_uncredentialed_head(status: int, reachable: bool) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"})

    assert await provider(handler).check() is reachable
    assert len(requests) == 1
    assert str(requests[0].url) == CHECK_URL
    assert requests[0].method == "HEAD"
    assert "x-subscription-token" not in requests[0].headers
    assert requests[0].content == b""


async def test_search_uses_post_and_does_not_log_query_or_key(caplog) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "type": "search",
                "web": {
                    "results": [
                        {
                            "title": " <b>공식</b> 문서 ",
                            "url": "https://EXAMPLE.com:443/docs#part",
                            "description": "<script>숨긴 글</script>첫째 &amp; 둘째\n 설명",
                        },
                        {
                            "title": "중복 주소",
                            "url": "https://example.com/docs#other",
                            "description": "중복",
                        },
                        {"title": "사설 주소", "url": "http://192.168.1.2:80/secrets"},
                    ]
                },
            },
        )

    caplog.set_level(logging.INFO, logger="httpx")
    response = await provider(handler).search("  합성질문abc9283\n 검색 ")
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and str(request.url) == SEARCH_URL
    assert request.headers["x-subscription-token"] == "synthetic-search-key"
    assert request.headers["accept-encoding"] == "identity"
    assert json.loads(request.content) == {
        "q": "합성질문abc9283 검색",
        "count": 5,
        "country": "KR",
        "search_lang": "ko",
        "result_filter": ["web"],
        "text_decorations": False,
    }
    assert response.results == (
        SearchResult("공식 문서", "https://example.com/docs", "첫째 & 둘째 설명"),
    )
    assert response.checked_at.utcoffset() == timedelta(0)
    assert "합성질문abc9283" not in caplog.text
    assert "synthetic-search-key" not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "//example.com",
        "http://localhost",
        "http://localhost.localdomain",
        "http://service.local",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.1.1",
        "http://127.0.0.1",
        "http://0.0.0.0",
        "http://100.64.0.1",
        "http://[::1]",
        "http://[fd00::1]",
        "http://[fe80::1]",
        "http://[::ffff:127.0.0.1]",
        "http://[fe80::1%25en0]",
        "http://127.1",
        "http://0177.0.0.1",
        "http://2130706433",
        "http://0x7f000001",
        "http://0x7f.0.0.1",
        "https://user:password@example.com/",
        "https://example.com@127.0.0.1",
        "https://example.com\\@127.0.0.1",
        "https://example.com:8000/admin",
        "https://example.com:invalid/",
        "https://example.com/\nprivate",
        "https://example.com/'><script>",
        "https://bad_host.example.com",
        "http://[invalid-ip]",
        "https://%6cocalhost/",
        "https://example.com/" + "a" * 2_048,
        None,
        {"url": "https://example.com/"},
    ],
)
def test_public_source_url_rejects_unsafe_or_local_addresses(url: object) -> None:
    assert public_source_url(url) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTPS://EXAMPLE.COM:443/a?b=1#part", "https://example.com/a?b=1"),
        ("http://example.com.:80", "http://example.com/"),
        ("https://8.8.8.8/docs", "https://8.8.8.8/docs"),
        ("https://[2001:4860:4860::8888]/", "https://[2001:4860:4860::8888]/"),
        ("https://한국.kr/문서", "https://xn--3e0b707e.kr/문서"),
    ],
)
def test_public_source_url_preserves_valid_public_links(raw: str, expected: str) -> None:
    assert public_source_url(raw) == expected


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (503, "unavailable"),
        (400, "invalid_response"),
        (302, "invalid_response"),
    ],
)
async def test_failure_does_not_follow_redirect_or_expose_body(status: int, code: str) -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="synthetic-search-key 합성질문abc9283 공급자 비밀 오류",
            headers={"Location": "http://127.0.0.1/private"},
        )

    with pytest.raises(SearchProviderError) as raised:
        await provider(handler).search("합성질문abc9283")
    assert raised.value.code == code
    assert len(requests) == 1
    assert "synthetic" not in str(raised.value)
    assert "abc9283" not in str(raised.value)
    assert "공급자 비밀 오류" not in str(raised.value)


@pytest.mark.parametrize("payload", [[], None, {}, {"web": []}, {"web": {"results": {}}}])
async def test_invalid_json_shape_is_rejected(payload: object) -> None:
    with pytest.raises(SearchProviderError) as raised:
        await provider(lambda request: httpx.Response(200, json=payload)).search("검색")
    assert raised.value.code == "invalid_response"


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
async def test_malformed_and_oversized_response_headers_are_rejected(response) -> None:
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
            yield b'{"type":"search"}'

    async def aclose(self):
        self.closed = True


async def test_streamed_response_without_length_is_bounded_and_closed() -> None:
    stream = TrackingStream(oversized=True)
    search = provider(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        )
    )
    with pytest.raises(SearchProviderError) as raised:
        await search.search("검색")
    assert raised.value.code == "invalid_response"
    assert stream.closed


async def test_cancel_search_closes_http_stream_without_swallowing_cancellation() -> None:
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


async def test_total_timeout_closes_stream_and_check_timeout_stays_false() -> None:
    stream = TrackingStream(block=True)
    search = provider(
        lambda request: httpx.Response(
            200, stream=stream, headers={"Content-Type": "application/json"}
        ),
        timeout_seconds=0.01,
    )
    with pytest.raises(SearchProviderError) as raised:
        await search.search("검색")
    assert raised.value.code == "timeout"
    assert stream.closed

    async def blocked(request):
        await asyncio.Event().wait()

    assert await provider(blocked, check_timeout_seconds=0.01).check() is False


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_errors_do_not_expose_exception_request_or_secret(error) -> None:
    def handler(request):
        raise error("합성질문abc9283 synthetic-search-key", request=request)

    search = provider(handler)
    assert await search.check() is False
    with pytest.raises(SearchProviderError) as raised:
        await search.search("합성질문abc9283")
    assert raised.value.code == ("timeout" if error is httpx.ReadTimeout else "unavailable")
    assert "synthetic" not in str(raised.value)
    assert "abc9283" not in str(raised.value)
    assert raised.value.__suppress_context__


async def test_query_and_result_limits_are_enforced_before_returning() -> None:
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "제목" * 500,
                            "url": f"https://example.com/{index}",
                            "description": "내용" * 2_000,
                        }
                        for index in range(4)
                    ]
                }
            },
        )

    search = provider(handler, max_query_chars=200, max_results=2)
    response = await search.search("질문 " * 500)
    assert len(response.results) == 2
    assert len(response.results[0].title) == 300
    assert len(response.results[0].snippet) == 2_000
    assert len(json.loads(requests[0].content)["q"]) <= 200
    with pytest.raises(SearchProviderError) as raised:
        await search.search("  \n ")
    assert raised.value.code == "invalid_query"
    assert len(requests) == 1
    assert len(prepare_search_query("x " * 100, max_chars=999).split()) == 75
    assert len(prepare_search_query("가" * 1_000, max_chars=999)) == 600


async def test_no_results_and_invalid_individual_results_are_safe() -> None:
    assert (
        await provider(lambda request: httpx.Response(200, json={"type": "search"})).search("검색")
    ).results == ()
    response = await provider(
        lambda request: httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        None,
                        {"title": 3, "url": "https://example.com/"},
                        {"title": "제목", "url": "https://example.com/", "description": {}},
                        {"title": "<b> </b>", "url": "https://example.com/"},
                        {"title": "제목", "url": "https://example.com/", "description": None},
                    ]
                }
            },
        )
    ).search("검색")
    assert response.results == (SearchResult("제목", "https://example.com/", ""),)


async def test_http_client_never_uses_proxy_environment_or_redirects(monkeypatch) -> None:
    client_class = httpx.AsyncClient
    configurations = []

    def client_factory(**kwargs):
        configurations.append(kwargs)
        return client_class(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    search = provider(lambda request: httpx.Response(200, json={"type": "search"}))
    await search.check()
    await search.search("검색")
    assert len(configurations) == 2
    assert all(configuration["trust_env"] is False for configuration in configurations)
    assert all(configuration["follow_redirects"] is False for configuration in configurations)
