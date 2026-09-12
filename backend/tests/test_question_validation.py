"""모델의 질문 카드 출력을 제한하며 잘못된 도구 호출은 실행하지 않는다."""

import json

import pytest

from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable, ToolCall
from backend.app.tools.questions import parse_question_card


@pytest.mark.parametrize(
    "body",
    [
        {"questions": []},
        {"questions": [{"question": " "}]},
        {"questions": [{"question": "가" * 301}]},
        {"questions": [{"question": "질문", "options": ["하나"]}]},
        {"questions": [{"question": "질문", "options": ["중복", "중복"]}]},
        {"questions": [{"question": "질문", "options": ["가" * 101, "둘"]}]},
        {"questions": [{"question": "질문"}] * 4},
        {"questions": [{"question": "질문", "permission": "admin"}]},
    ],
)
def test_invalid_model_question_is_rejected(body):
    delta = ProviderDelta(
        final=True,
        finish_reason="tool_calls",
        tool_calls=(ToolCall("q", "ask_user_question", json.dumps(body)),),
    )
    with pytest.raises(ProviderUnavailable):
        parse_question_card(delta)


def test_question_tool_only_accepts_complete_single_call():
    body = '{"questions":[{"question":"필요한 내용?","options":[]}]}'
    call = ToolCall("q", "ask_user_question", body)
    assert parse_question_card(
        ProviderDelta(final=True, finish_reason="tool_calls", tool_calls=(call,))
    ) == {"questions": [{"question": "필요한 내용?", "options": []}]}
    assert parse_question_card(ProviderDelta(final=True, finish_reason="stop")) is None
    for calls, reason in (
        ((call,), "length"),
        ((call, call), "tool_calls"),
        ((), "tool_calls"),
        ((ToolCall("q", "web_search", body),), "tool_calls"),
    ):
        with pytest.raises(ProviderUnavailable):
            parse_question_card(ProviderDelta(final=True, finish_reason=reason, tool_calls=calls))
