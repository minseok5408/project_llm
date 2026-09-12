"""검색어의 원문 근거·외부 전송 범위와 도구 스트림 계약을 검증한다."""

import json

import httpx
import pytest

from backend.app.llm.protocol import ProviderUnavailable, ToolCall
from backend.app.llm.providers.tool_calls import ToolCallBuffer
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.web_search.planning import SEARCH_TOOLS, query_entries, validate_call
from backend.tests.test_provider_usage import event, usage_event
from backend.tests.test_provider_usage import mlx as mlx


def call(terms, **extra):
    return ToolCall("call_1", "web_search", json.dumps({"terms": terms, **extra}))


def test_context_query_only_uses_bounded_verbatim_user_terms():
    question = "그 회사 최신 뉴스를 검색해줘"
    entries = query_entries(
        {
            "messages": [
                {"role": "system", "content": "시스템 요약에는 비공개정보가 있다"},
                {
                    "role": "user",
                    "content": "내 이름은 김검증이고 내부 메모는 private-sentinel이다",
                },
                {"role": "assistant", "content": "다른 회사 이름을 임의로 말함"},
                {"role": "user", "content": "삼성전자에 대해 설명해줘"},
                {"role": "user", "content": question},
            ]
        },
        question,
    )
    assert entries == {0: question, 1: "삼성전자에 대해 설명해줘"}
    assert (
        validate_call(
            call([{"message": 1, "text": "삼성전자"}, {"message": 0, "text": "최신 뉴스"}]),
            entries,
            500,
        )
        == "삼성전자 최신 뉴스"
    )


@pytest.mark.parametrize(
    "terms",
    [
        [{"message": 1, "text": "없는 이름"}],
        [{"message": 4, "text": "삼성전자"}],
        [{"message": True, "text": "삼성전자"}],
        [{"message": 1, "text": " "}],
        [{"message": 1, "text": "x" * 81}],
        [{"message": 1, "text": "test@example.com"}],
        [],
    ],
)
def test_invalid_or_excessive_context_terms_are_rejected(terms):
    with pytest.raises(ValueError):
        validate_call(
            call(terms), {0: "질문", 1: "삼성전자 " + "x" * 81 + " test@example.com"}, 500
        )


@pytest.mark.parametrize(
    "tool",
    [
        ToolCall("", "web_search", "{}"),
        ToolCall("ok", "shell", "{}"),
        ToolCall("ok", "finish_search", '{"reason":"sufficient","command":"other"}'),
        call([{"message": 0, "text": "질문"}], query="추가 전송"),
    ],
)
def test_unregistered_tools_and_extra_arguments_are_rejected(tool):
    with pytest.raises(ValueError):
        validate_call(tool, {0: "질문"}, 500)


@pytest.mark.parametrize(
    "payload",
    [
        [{"index": 1}],
        [{"index": 0, "function": {"arguments": "x" * 4097}}],
        [{"index": 0, "type": "shell"}],
        [{"index": 0}, {"index": 0}],
    ],
)
def test_tool_stream_has_strict_size_and_single_call_limits(payload):
    with pytest.raises(ProviderUnavailable):
        ToolCallBuffer().feed(payload)


@pytest.mark.asyncio
async def test_mlx_tool_fragments_and_usage_keep_same_tool_template(mlx):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 123})
        return httpx.Response(
            200,
            content=(
                event(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "finish_search",
                                                "arguments": '{"reason":',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
                + event(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": '"sufficient"}'}}
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    }
                )
                + usage_event(123, 20)
                + event("[DONE]")
            ),
        )

    provider = mlx(handler)
    messages, options = [ChatMessage(role="user", content="검색 판단")], GenerationOptions()
    assert await provider.count_tools(messages, options, SEARCH_TOOLS) == 123
    deltas = [delta async for delta in provider.stream_tools(messages, options, SEARCH_TOOLS)]
    assert requests[0]["tools"] == requests[1]["tools"] == SEARCH_TOOLS
    assert deltas[-1].tool_calls == (
        ToolCall("call_1", "finish_search", '{"reason":"sufficient"}'),
    )
    assert deltas[-1].finish_reason == "tool_calls"
    assert deltas[-1].input_tokens == 123 and deltas[-1].output_tokens == 20
    assert all(not delta.text for delta in deltas)
