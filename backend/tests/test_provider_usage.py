import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers import (
    MlxServerProvider,
    MockProvider,
    ProviderDelta,
    ProviderUnavailable,
)
from backend.app.schemas import ChatMessage, GenerationOptions


def settings() -> Settings:
    return Settings(_env_file=None, database_enabled=False, llm_backend="mlx")


def event(value: dict | str) -> bytes:
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return f"data: {data}\n\n".encode()


def text_event(text: str, **extra: str) -> bytes:
    return event({"choices": [{"delta": {"content": text, **extra}}]})


def usage_event(input_tokens: int = 23, output_tokens: int = 7) -> bytes:
    return event(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }
    )


def finish_event(reason: object, *, usage: bool = False) -> bytes:
    payload = {"choices": [{"delta": {}, "finish_reason": reason}]}
    if usage:
        payload["usage"] = {
            "prompt_tokens": 23,
            "completion_tokens": 7,
            "total_tokens": 30,
        }
    return event(payload)


def measured_event(text: str = "", *, logprobs: object = None, **extra: str) -> bytes:
    return event({"choices": [{"delta": {"content": text, **extra}, "logprobs": logprobs}]})


def measured_tokens(*tokens: str) -> dict:
    return {
        "content": [
            {"token": token, "logprob": -0.5, "bytes": list(token.encode())} for token in tokens
        ]
    }


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def mlx(monkeypatch: pytest.MonkeyPatch) -> Callable:
    client_type = httpx.AsyncClient

    def build(handler: Callable) -> MlxServerProvider:
        transport = httpx.MockTransport(handler)

        def client(*args, **kwargs):
            return client_type(*args, transport=transport, **kwargs)

        monkeypatch.setattr("backend.app.providers.httpx.AsyncClient", client)
        return MlxServerProvider(settings())

    return build


@pytest.mark.parametrize("thinking", [False, True])
async def test_count_and_generation_use_the_same_normalized_prompt(mlx, thinking: bool) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith("responses/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 91})
        return httpx.Response(
            200, content=text_event("정답") + usage_event(91, 13) + event("[DONE]")
        )

    provider = mlx(handle)
    messages = [
        ChatMessage(role="system", content="첫 시스템 안내"),
        ChatMessage(role="user", content="이전 질문"),
        ChatMessage(role="assistant", content="이전 답변"),
        ChatMessage(role="system", content="추가 시스템 안내"),
        ChatMessage(role="user", content="한글 질문 🦊"),
    ]
    options = GenerationOptions(thinking=thinking, max_tokens=32)
    assert await provider.count_input(messages, options) == 91
    deltas = [delta async for delta in provider.stream(messages, options)]

    count_path, count_payload = requests[0]
    stream_path, stream_payload = requests[1]
    assert count_path == "/v1/responses/input_tokens"
    assert stream_path == "/v1/chat/completions"
    assert count_payload["input"] == stream_payload["messages"]
    assert count_payload["input"][0] == {
        "role": "system",
        "content": "첫 시스템 안내\n\n추가 시스템 안내",
    }
    assert count_payload["enable_thinking"] is stream_payload["enable_thinking"] is thinking
    assert count_payload["max_output_tokens"] == stream_payload["max_tokens"] == 32
    assert stream_payload["stream_options"] == {"include_usage": True}
    assert stream_payload["logprobs"] is True
    assert stream_payload["top_logprobs"] == 0
    assert messages[3].role == "system"
    assert deltas == [
        ProviderDelta(text="정답"),
        ProviderDelta(input_tokens=91, output_tokens=13, final=True),
    ]


@pytest.mark.parametrize("value", [None, True, -1, 0, 1.5, "23", 2**63])
async def test_invalid_input_count_fails_before_generation(mlx, value: object) -> None:
    provider = mlx(lambda _: httpx.Response(200, json={"input_tokens": value}))
    with pytest.raises(ProviderUnavailable) as caught:
        await provider.count_input([ChatMessage(role="user", content="질문")], GenerationOptions())
    assert caught.value.request_started is False


async def test_missing_count_endpoint_is_explicit_and_does_not_expose_response(mlx) -> None:
    provider = mlx(lambda _: httpx.Response(404, text="private-model-path-and-prompt"))
    with pytest.raises(ProviderUnavailable, match="입력 토큰 계산 API를 지원하지") as caught:
        await provider.count_input([ChatMessage(role="user", content="질문")], GenerationOptions())
    assert caught.value.request_started is False
    assert "private-model" not in str(caught.value)


async def test_stream_delivers_first_text_before_terminal_usage_and_closes(mlx) -> None:
    finish = asyncio.Event()

    class WaitingChunks(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            first = text_event("안녕하세요")
            # 전송 바이트가 UTF-8 문자 중간에서 나뉘어도 SSE 디코더가 복원해야 한다.
            cut = first.index("안".encode()) + 1
            yield first[:cut]
            yield first[cut:]
            await finish.wait()
            yield usage_event(41, 18)
            yield event("[DONE]")

    upstream = WaitingChunks([])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    iterator = provider.stream([ChatMessage(role="user", content="질문")], GenerationOptions())
    first = await asyncio.wait_for(anext(iterator), timeout=1)
    assert first == ProviderDelta(text="안녕하세요")
    assert not finish.is_set()
    finish.set()
    remaining = [delta async for delta in iterator]
    assert remaining == [ProviderDelta(input_tokens=41, output_tokens=18, final=True)]
    assert upstream.closed


@pytest.mark.parametrize("marker", ["<think>", "<|START_THINKING|>", "<|channel>thought"])
async def test_hidden_reasoning_never_leaks_across_split_markers(mlx, marker: str) -> None:
    closing = {
        "<think>": "</think>",
        "<|START_THINKING|>": "<|END_THINKING|>",
        "<|channel>thought": "<channel|>",
    }[marker]
    # 각 글자를 별도 조각으로 보내 모든 태그 경계에서 분할을 재현한다.
    pieces = [text_event(char) for char in f"{marker}비공개 생각{closing}공개 답변"]
    pieces.insert(0, text_event("", reasoning="비공개 서버 추론", reasoning_content="내부 추론"))
    upstream = Chunks([*pieces, usage_event(51, 29), event("[DONE]")])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions(thinking=True)
        )
    ]
    assert "".join(delta.text for delta in deltas) == "공개 답변"
    assert deltas[-1] == ProviderDelta(input_tokens=51, output_tokens=29, final=True)


async def test_reasoning_only_completion_keeps_actual_usage_without_visible_text(mlx) -> None:
    upstream = Chunks(
        [
            text_event("", reasoning="아직 생각 중"),
            text_event("<think>계속 생각"),
            usage_event(21, 32),
        ]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")],
            GenerationOptions(thinking=True, max_tokens=32),
        )
    ]
    assert deltas == [ProviderDelta(input_tokens=21, output_tokens=32, final=True)]


async def test_visible_answer_keeps_a_trailing_character_that_could_start_a_tag(mlx) -> None:
    provider = mlx(
        lambda _: httpx.Response(200, content=text_event("작다는 기호: <") + usage_event())
    )
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="기호 설명")], GenerationOptions()
        )
    ]
    assert "".join(delta.text for delta in deltas) == "작다는 기호: <"
    assert deltas[-1] == ProviderDelta(input_tokens=23, output_tokens=7, final=True)


@pytest.mark.parametrize(
    "ending",
    [
        b"",
        event("[DONE]"),
        event({"error": "private-prompt-password"}),
        b"data: invalid-json\n\n",
        event({"usage": {"prompt_tokens": 23, "completion_tokens": True, "total_tokens": 24}}),
        event({"usage": {"prompt_tokens": 23, "completion_tokens": 2, "total_tokens": 26}}),
        usage_event(23, 7) + usage_event(23, 8),
        usage_event(23, 7) + text_event("확정 이후 추가 본문"),
    ],
)
async def test_uncertain_or_invalid_terminal_usage_never_becomes_a_final_delta(
    mlx, ending: bytes
) -> None:
    upstream = Chunks([text_event("부분 답변"), ending])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    received = []
    with pytest.raises(ProviderUnavailable) as caught:
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        ):
            received.append(delta)
    assert caught.value.request_started is True
    assert "private-prompt" not in str(caught.value)
    assert received == [ProviderDelta(text="부분 답변")]
    assert upstream.closed


@pytest.mark.parametrize(
    ("error_type", "started"),
    [
        (httpx.ConnectError, False),
        (httpx.ConnectTimeout, False),
        (httpx.PoolTimeout, False),
        (httpx.ReadError, True),
        (httpx.ReadTimeout, True),
        (httpx.WriteError, True),
        (httpx.RemoteProtocolError, True),
    ],
)
async def test_transport_errors_distinguish_safe_release_from_uncertain_usage(
    mlx, error_type, started
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise error_type("private-request-data", request=request)

    provider = mlx(handle)
    with pytest.raises(ProviderUnavailable) as caught:
        _ = [
            delta
            async for delta in provider.stream(
                [ChatMessage(role="user", content="질문")], GenerationOptions()
            )
        ]
    assert caught.value.request_started is started
    assert "private-request" not in str(caught.value)


async def test_http_error_does_not_assume_that_generation_was_never_started(mlx) -> None:
    provider = mlx(lambda _: httpx.Response(500, text="private-prompt-and-server-path"))
    with pytest.raises(ProviderUnavailable) as caught:
        _ = [
            delta
            async for delta in provider.stream(
                [ChatMessage(role="user", content="질문")], GenerationOptions()
            )
        ]
    assert caught.value.request_started is True
    assert "private-prompt" not in str(caught.value)


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ConnectError])
async def test_midstream_disconnect_does_not_turn_partial_text_into_usage(mlx, error_type) -> None:
    upstream = Chunks([text_event("부분 답변")], error=error_type("connection lost"))
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    iterator = provider.stream([ChatMessage(role="user", content="질문")], GenerationOptions())
    assert await anext(iterator) == ProviderDelta(text="부분 답변")
    with pytest.raises(ProviderUnavailable) as caught:
        await anext(iterator)
    assert caught.value.request_started is True
    assert upstream.closed


async def test_cancelling_consumer_closes_upstream_without_fabricating_usage(mlx) -> None:
    waiting = asyncio.Event()

    class WaitingChunks(Chunks):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield text_event("부분 답변")
            waiting.set()
            await asyncio.Event().wait()

    upstream = WaitingChunks([])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    iterator = provider.stream([ChatMessage(role="user", content="질문")], GenerationOptions())
    assert await anext(iterator) == ProviderDelta(text="부분 답변")
    next_delta = asyncio.create_task(anext(iterator))
    await asyncio.wait_for(waiting.wait(), timeout=1)
    next_delta.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_delta
    assert upstream.closed


@pytest.mark.parametrize(("limit", "reason"), [(1, "length"), (3, "length"), (128, "stop")])
async def test_mock_accounts_for_the_tokens_it_consumes_and_obeys_output_limit(
    limit: int, reason: str
) -> None:
    provider = MockProvider(settings(), delay_seconds=0)
    messages = [ChatMessage(role="user", content="모의 응답 토큰 확인")]
    options = GenerationOptions(max_tokens=limit)
    deltas = [delta async for delta in provider.stream(messages, options)]
    tokens = [delta.text for delta in deltas if not delta.final]
    assert all(tokens)
    assert 0 < len(tokens) <= limit
    assert deltas[-1] == ProviderDelta(
        input_tokens=await provider.count_input(messages, options),
        output_tokens=len(tokens),
        final=True,
        received_output_tokens=len(tokens),
        finish_reason=reason,
    )
    assert [delta.received_output_tokens for delta in deltas if not delta.final] == list(
        range(1, len(tokens) + 1)
    )
    assert len(tokens) == len("".join(tokens).split())
    assert sum(delta.final for delta in deltas) == 1


async def test_mock_does_not_report_truncation_when_the_answer_exactly_fits_the_limit() -> None:
    provider = MockProvider(settings(), delay_seconds=0)
    messages = [ChatMessage(role="user", content="모의 응답 토큰 확인")]
    complete = [
        delta async for delta in provider.stream(messages, GenerationOptions(max_tokens=128))
    ]
    exact = [
        delta
        async for delta in provider.stream(
            messages, GenerationOptions(max_tokens=complete[-1].output_tokens)
        )
    ]
    assert exact == complete
    assert exact[-1].finish_reason == "stop"


@pytest.mark.parametrize("reason", ["stop", "length"])
@pytest.mark.parametrize("same_frame", [False, True])
async def test_finish_reason_waits_for_final_usage_in_the_same_or_a_later_frame(
    mlx, reason: str, same_frame: bool
) -> None:
    ending = finish_event(reason, usage=same_frame)
    if not same_frame:
        ending += usage_event()
    upstream = Chunks([text_event("답변"), ending, event("[DONE]")])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        )
    ]
    assert deltas == [
        ProviderDelta(text="답변"),
        ProviderDelta(input_tokens=23, output_tokens=7, final=True, finish_reason=reason),
    ]
    assert upstream.closed


@pytest.mark.parametrize("reason", [None, "content_filter", "tool_calls", "unknown", {}, 7])
async def test_missing_or_unsupported_finish_reason_is_not_inferred_from_the_limit(
    mlx, reason: object
) -> None:
    provider = mlx(
        lambda _: httpx.Response(200, content=text_event("답변") + finish_event(reason, usage=True))
    )
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions(max_tokens=7)
        )
    ]
    assert deltas[-1] == ProviderDelta(input_tokens=23, output_tokens=7, final=True)


async def test_repeated_finish_reason_and_usage_before_reason_preserve_terminal_usage(mlx) -> None:
    upstream = Chunks(
        [text_event("답변"), usage_event(), finish_event("length"), finish_event("length")]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        )
    ]
    assert deltas[-1] == ProviderDelta(
        input_tokens=23, output_tokens=7, final=True, finish_reason="length"
    )


async def test_conflicting_finish_reasons_do_not_emit_a_final_delta(mlx) -> None:
    upstream = Chunks(
        [text_event("부분 답변"), finish_event("length"), finish_event("stop", usage=True)]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    received = []
    with pytest.raises(ProviderUnavailable, match="종료 이유가 서로 다릅니다"):
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        ):
            received.append(delta)
    assert received == [ProviderDelta(text="부분 답변")]
    assert upstream.closed


async def test_finish_reason_alone_does_not_replace_missing_final_usage(mlx) -> None:
    provider = mlx(
        lambda _: httpx.Response(200, content=text_event("부분 답변") + finish_event("length"))
    )
    received = []
    with pytest.raises(ProviderUnavailable, match="최종 실제 토큰 사용량"):
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        ):
            received.append(delta)
    assert received == [ProviderDelta(text="부분 답변")]


async def test_length_termination_preserves_hidden_usage_without_exposing_reasoning(mlx) -> None:
    upstream = Chunks(
        [
            measured_event("<think>비공개 추론", logprobs=measured_tokens("숨긴 토큰")),
            finish_event("length"),
            usage_event(31, 1),
            event("[DONE]"),
        ]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")],
            GenerationOptions(thinking=True, max_tokens=1),
        )
    ]
    assert deltas == [
        ProviderDelta(text="", received_output_tokens=1),
        ProviderDelta(
            input_tokens=31,
            output_tokens=1,
            final=True,
            received_output_tokens=1,
            finish_reason="length",
        ),
    ]
    assert "비공개" not in repr(deltas)


async def test_received_counts_include_hidden_reasoning_and_empty_text_without_exposing_tokens(
    mlx,
) -> None:
    upstream = Chunks(
        [
            measured_event(
                logprobs=measured_tokens("비공개 추론 토큰"), reasoning="비공개 추론 본문"
            ),
            measured_event(logprobs=measured_tokens("UTF-8 중간 토큰")),
            measured_event("공개 답변", logprobs=measured_tokens("공개", "답변")),
            usage_event(31, 5),
            event("[DONE]"),
        ]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions(thinking=True)
        )
    ]
    assert deltas == [
        ProviderDelta(text="", received_output_tokens=1),
        ProviderDelta(text="", received_output_tokens=2),
        ProviderDelta(text="공개 답변", received_output_tokens=4),
        ProviderDelta(input_tokens=31, output_tokens=5, final=True, received_output_tokens=4),
    ]
    assert "비공개" not in repr(deltas)
    assert "UTF-8" not in repr(deltas)
    assert upstream.closed


async def test_thinking_delimiters_split_across_frames_still_report_received_tokens_immediately(
    mlx,
) -> None:
    fragments = ["<thi", "nk>", "추론 본문", "</thi", "nk>", "보이는 답변"]
    upstream = Chunks(
        [
            *(
                measured_event(fragment, logprobs=measured_tokens("검증된 토큰"))
                for fragment in fragments
            ),
            usage_event(28, 6),
        ]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions(thinking=True)
        )
    ]
    assert [delta.received_output_tokens for delta in deltas[:-1]] == [1, 2, 3, 4, 5, 6]
    assert [delta.text for delta in deltas[:-1]] == ["", "", "", "", "", "보이는 답변"]
    assert deltas[-1].output_tokens == deltas[-1].received_output_tokens == 6


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        {"content": []},
        {"content": True},
        {"content": [None]},
        {"content": [{"logprob": -0.5}]},
        {"content": [{"token": "토큰", "logprob": True}]},
        {"content": [{"token": "토큰", "logprob": float("nan")}]},
        {"content": [{"token": "토큰", "logprob": float("-inf")}]},
        {"content": [{"token": "토큰", "logprob": 0.5}]},
        {"content": [{"token": "토큰", "logprob": -(10**400)}]},
        {"content": [{"token": "토큰", "logprob": -0.5, "bytes": [256]}]},
        {"content": [{"token": "토큰", "logprob": -0.5, "bytes": [True]}]},
        measured_tokens("초과1", "초과2", "초과3", "초과4"),
    ],
)
async def test_unconfirmed_frames_do_not_invent_counts_or_discard_valid_final_usage(
    mlx, malformed
) -> None:
    upstream = Chunks(
        [
            measured_event("첫 부분 ", logprobs=measured_tokens("첫토큰")),
            measured_event("중간 부분 ", logprobs=malformed),
            measured_event("끝", logprobs=measured_tokens("마지막토큰")),
            usage_event(31, 4),
        ]
    )
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions(max_tokens=4)
        )
    ]
    assert [delta.received_output_tokens for delta in deltas] == [1, 1, 2, 2]
    assert "".join(delta.text for delta in deltas) == "첫 부분 중간 부분 끝"
    assert deltas[-1] == ProviderDelta(
        input_tokens=31, output_tokens=4, final=True, received_output_tokens=2
    )


async def test_no_valid_logprobs_keeps_received_usage_unknown_even_with_visible_text(mlx) -> None:
    provider = mlx(lambda _: httpx.Response(200, content=text_event("답변") + usage_event(20, 3)))
    deltas = [
        delta
        async for delta in provider.stream(
            [ChatMessage(role="user", content="질문")], GenerationOptions()
        )
    ]
    assert all(delta.received_output_tokens is None for delta in deltas)
    assert deltas[-1].final is True
    assert deltas[-1].output_tokens == 3


async def test_closing_after_confirmed_hidden_token_closes_upstream_without_waiting_for_usage(
    mlx,
) -> None:
    entered_wait = False

    class UnfinishedChunks(Chunks):
        async def __aiter__(self):
            nonlocal entered_wait
            yield measured_event(logprobs=measured_tokens("숨긴 토큰"), reasoning="내부 생각")
            entered_wait = True
            await asyncio.Event().wait()

    upstream = UnfinishedChunks([])
    provider = mlx(lambda _: httpx.Response(200, stream=upstream))
    iterator = provider.stream(
        [ChatMessage(role="user", content="질문")], GenerationOptions(thinking=True)
    )
    first = await asyncio.wait_for(anext(iterator), timeout=1)
    assert first == ProviderDelta(text="", received_output_tokens=1)
    await asyncio.wait_for(iterator.aclose(), timeout=1)
    assert upstream.closed
    assert entered_wait is False
