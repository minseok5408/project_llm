"""언어 안내는 모델 입력 복사본에만 추가하고 계산·생성 입력을 동일하게 유지한다."""

import json

import httpx
import pytest

from backend.app.context.builder import compose_context
from backend.app.context.compaction import SUMMARY_PROMPT
from backend.app.context.language import LANGUAGE_POLICY, LANGUAGE_REMINDER
from backend.app.llm.providers.common import _normalized_messages
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.questions import QUESTION_PROMPT, QUESTION_TOOLS
from backend.app.tools.web_search.planning import query_entries
from backend.tests.test_provider_usage import event, text_event, usage_event
from backend.tests.test_provider_usage import mlx as mlx


@pytest.mark.parametrize(
    "original",
    [
        '职业规划를 번역하고 print("你好")를 인용해줘',
        "Hello, world. 한국어로 번역해줘",
        "안녕하세요. 영어로만 번역해줘",
        "Please explain career planning.",
    ],
)
@pytest.mark.parametrize("tools", [False, True])
async def test_language_count_and_generation_preserve_original_and_match_wire_input(
    mlx, original, tools
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if request.url.path.endswith("responses/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 91})
        return httpx.Response(
            200,
            content=text_event('인용: 职业规划 / print("你好")')
            + usage_event(91, 13)
            + event("[DONE]"),
        )

    provider = mlx(handle)
    messages = compose_context([], original)
    if tools:
        messages.insert(1, ChatMessage(role="system", content=QUESTION_PROMPT))
    before = [item.model_dump() for item in messages]
    queries_before = query_entries({"messages": before}, original)
    options = GenerationOptions()
    if tools:
        assert await provider.count_tools(messages, options, QUESTION_TOOLS) == 91
        stream = provider.stream_tools(messages, options, QUESTION_TOOLS)
    else:
        assert await provider.count_input(messages, options) == 91
        stream = provider.stream(messages, options)
    output = "".join([delta.text async for delta in stream])
    assert output == '인용: 职业规划 / print("你好")'
    assert requests[0]["input"] == requests[1]["messages"]
    last = requests[0]["input"][-1]["content"]
    assert last == original + "\n\n[Response language for this turn]\n" + LANGUAGE_REMINDER
    assert [item.model_dump() for item in messages] == before
    assert (
        query_entries({"messages": [item.model_dump() for item in messages]}, original)
        == queries_before
    )
    assert _normalized_messages(messages) == requests[0]["input"]


def test_internal_summary_or_user_text_cannot_activate_language_reminder():
    for system in (SUMMARY_PROMPT, "검색 계획을 JSON으로 작성하세요."):
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=LANGUAGE_POLICY),
        ]
        assert _normalized_messages(messages) == [item.model_dump() for item in messages]
    messages = [ChatMessage(role="user", content=LANGUAGE_POLICY)]
    assert _normalized_messages(messages) == [item.model_dump() for item in messages]
