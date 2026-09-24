"""MLX 서버의 문맥 계산·스트리밍·추론 표시·사용량을 처리한다."""

import json
import math
from collections.abc import AsyncIterator, Sequence

import httpx

from backend.app.config import Settings
from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable
from backend.app.llm.providers.common import _normalized_messages
from backend.app.llm.providers.tool_calls import ToolCallBuffer
from backend.app.schemas import ChatMessage, GenerationOptions, ProviderStatus

TOOL_SAMPLING_OVERRIDES = {"temperature": 0.0, "presence_penalty": 0.0}


class _VisibleContent:
    """서버가 content에 남긴 추론 구간도 조각 경계에 관계없이 제외한다."""

    _markers = {
        "<think>": "</think>",
        "<|channel>thought": "<channel|>",
        "<|START_THINKING|>": "<|END_THINKING|>",
    }

    def __init__(self) -> None:
        self._buffer = ""
        self._closing: str | None = None

    def feed(self, text: str) -> str:
        self._buffer += text
        visible: list[str] = []
        while self._buffer:
            markers = (self._closing,) if self._closing else tuple(self._markers)
            matches = [
                (position, marker)
                for marker in markers
                if (position := self._buffer.find(marker)) >= 0
            ]
            if matches:
                position, marker = min(matches)
                if self._closing is None:
                    visible.append(self._buffer[:position])
                    self._closing = self._markers[marker]
                else:
                    self._closing = None
                self._buffer = self._buffer[position + len(marker) :]
                continue

            held = max(
                (
                    size
                    for marker in markers
                    for size in range(1, len(marker))
                    if self._buffer.endswith(marker[:size])
                ),
                default=0,
            )
            if self._closing is None:
                visible.append(self._buffer[:-held] if held else self._buffer)
            # 추론 본문은 누적하지 않고 경계를 판별하는 데 필요한 접미사만 남긴다.
            self._buffer = self._buffer[-held:] if held else ""
            break
        return "".join(visible)

    def finish(self) -> str:
        # 완성되지 않은 태그 접두사와 겹치는 일반 답변 문자도 종료 시에는 보존한다.
        visible = self._buffer if self._closing is None else ""
        self._buffer = ""
        return visible


def _token_count(value: object) -> int:
    if type(value) is not int or not 0 <= value < 2**63:
        raise ProviderUnavailable("MLX 서버의 실제 토큰 사용량 응답이 올바르지 않습니다.")
    return value


def _usage_delta(value: object) -> ProviderDelta:
    if not isinstance(value, dict):
        raise ProviderUnavailable("MLX 서버의 실제 토큰 사용량 응답이 올바르지 않습니다.")
    input_tokens = _token_count(value.get("prompt_tokens"))
    output_tokens = _token_count(value.get("completion_tokens"))
    total_tokens = _token_count(value.get("total_tokens"))
    if input_tokens == 0 or total_tokens != input_tokens + output_tokens:
        raise ProviderUnavailable("MLX 서버의 실제 토큰 사용량 응답이 올바르지 않습니다.")
    return ProviderDelta(input_tokens=input_tokens, output_tokens=output_tokens, final=True)


def _confirmed_tokens(value: object, remaining: int) -> int:
    """서버가 실제 토큰으로 명시한 유효 항목만 세고 내용·확률은 보관하지 않는다."""
    if not isinstance(value, dict):
        return 0
    entries = value.get("content")
    if not isinstance(entries, list) or not 1 <= len(entries) <= remaining:
        return 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("token"), str):
            return 0
        probability = entry.get("logprob")
        if type(probability) not in (int, float) or probability > 0:
            return 0
        try:
            if not math.isfinite(probability):
                return 0
        except OverflowError:
            return 0
        token_bytes = entry.get("bytes")
        if token_bytes is not None and (
            not isinstance(token_bytes, list)
            or any(type(item) is not int or not 0 <= item <= 255 for item in token_bytes)
        ):
            return 0
    return len(entries)


class MlxServerProvider:
    """mlx-vlm.server가 제공하는 OpenAI 호환 API의 어댑터."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = f"{settings.llm_base_url.rstrip('/')}/"
        self._timeout = httpx.Timeout(
            connect=5,
            read=settings.llm_request_timeout_seconds,
            write=30,
            pool=5,
        )

    async def status(self) -> ProviderStatus:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=2, trust_env=False
            ) as client:
                response = await client.get("models")
                response.raise_for_status()
            return ProviderStatus(
                backend="mlx",
                ready=True,
                model=self.settings.llm_model_id,
                detail=(
                    f"MLX-VLM · {self.settings.llm_context_window // 1024}K 문맥 · "
                    f"동시 생성 {self.settings.llm_max_concurrent_generations}건"
                ),
            )
        except (httpx.HTTPError, ValueError):
            return ProviderStatus(
                backend="mlx",
                ready=False,
                model=self.settings.llm_model_id,
                detail="MLX 서버를 기다리는 중 · 127.0.0.1:8080",
            )

    async def count_input(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
        *,
        tools: list[dict] | None = None,
    ) -> int:
        # 이미 실행 중인 모델 서버의 동일한 채팅 템플릿·토크나이저를 사용한다.
        # 게이트웨이에서 모델 가중치나 별도 토크나이저를 로드하지 않는다.
        payload = {
            "model": self.settings.llm_model_id,
            "input": _normalized_messages(messages),
            "max_output_tokens": options.max_tokens,
            "enable_thinking": options.thinking,
        }
        if tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout, trust_env=False
            ) as client:
                response = await client.post("responses/input_tokens", json=payload)
                if response.status_code == 404:
                    raise ProviderUnavailable(
                        "MLX 서버가 실제 입력 토큰 계산 API를 지원하지 않습니다.",
                        request_started=False,
                    )
                response.raise_for_status()
                data = response.json()
                count = _token_count(data.get("input_tokens") if isinstance(data, dict) else None)
                if count == 0:
                    raise ValueError("empty input tokens")
                return count
        except (httpx.HTTPError, ValueError, ProviderUnavailable) as error:
            message = (
                str(error)
                if isinstance(error, ProviderUnavailable)
                else "MLX 서버에서 실제 입력 토큰 수를 확인하지 못했습니다."
            )
            raise ProviderUnavailable(message, request_started=False) from None

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
        *,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ProviderDelta]:
        thinking = options.thinking
        payload = {
            "model": self.settings.llm_model_id,
            "messages": _normalized_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "logprobs": True,
            "top_logprobs": 0,
            "max_tokens": options.max_tokens,
            "temperature": 1.0 if thinking else 0.7,
            "top_p": 0.95 if thinking else 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0 if thinking else 1.5,
            "repetition_penalty": 1.0,
            "enable_thinking": thinking,
        }
        if tools:
            # auto는 입력 계산과 다른 강제 지시를 서버가 덧붙이지 않도록 한다.
            payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
            # 도구 인자 선택에는 답변 문장의 다양성을 위한 패널티를 적용하지 않는다.
            payload.update(TOOL_SAMPLING_OVERRIDES)

        usage: ProviderDelta | None = None
        received: int | None = None
        finish_reason: str | None = None
        content = _VisibleContent()
        tool_calls = ToolCallBuffer()
        response_received = False
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                trust_env=False,
            ) as client:
                async with client.stream("POST", "chat/completions", json=payload) as response:
                    response_received = True
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue

                        raw_data = line[5:].strip()
                        if not raw_data:
                            continue
                        if raw_data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(raw_data)
                        except json.JSONDecodeError:
                            raise ProviderUnavailable(
                                "MLX 서버의 스트리밍 응답이 올바르지 않습니다."
                            ) from None
                        if not isinstance(chunk, dict) or "error" in chunk:
                            raise ProviderUnavailable("MLX 서버의 응답 생성이 중단되었습니다.")
                        if chunk.get("usage") is not None:
                            current_usage = _usage_delta(chunk["usage"])
                            if usage is not None and usage != current_usage:
                                raise ProviderUnavailable(
                                    "MLX 서버의 실제 토큰 사용량 응답이 서로 다릅니다."
                                )
                            usage = current_usage

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        if not isinstance(choices, list) or not isinstance(choices[0], dict):
                            raise ProviderUnavailable(
                                "MLX 서버의 스트리밍 응답이 올바르지 않습니다."
                            )
                        current_reason = choices[0].get("finish_reason")
                        if isinstance(current_reason, str) and (
                            current_reason in ("stop", "length")
                            or (tools and current_reason == "tool_calls")
                        ):
                            if finish_reason is not None and finish_reason != current_reason:
                                raise ProviderUnavailable(
                                    "MLX 서버의 답변 종료 이유가 서로 다릅니다."
                                )
                            # 종료 이유와 최종 사용량이 서로 다른 프레임에 올 수 있다.
                            finish_reason = current_reason
                        confirmed = _confirmed_tokens(
                            choices[0].get("logprobs"), options.max_tokens - (received or 0)
                        )
                        if confirmed:
                            if usage is not None:
                                raise ProviderUnavailable(
                                    "MLX 서버가 사용량 확정 후 추가 토큰을 보냈습니다."
                                )
                            received = (received or 0) + confirmed
                        delta = choices[0].get("delta") or {}
                        if not isinstance(delta, dict):
                            raise ProviderUnavailable(
                                "MLX 서버의 스트리밍 응답이 올바르지 않습니다."
                            )
                        if delta.get("tool_calls"):
                            if not tools or usage is not None:
                                raise ProviderUnavailable("허용하지 않은 도구 호출을 받았습니다.")
                            tool_calls.feed(delta["tool_calls"])
                        text = delta.get("content")
                        visible = ""
                        if isinstance(text, str) and text:
                            if usage is not None:
                                raise ProviderUnavailable(
                                    "MLX 서버가 사용량 확정 후 추가 응답을 보냈습니다."
                                )
                            # reasoning·reasoning_content는 저장하거나 사용자에게 전달하지 않는다.
                            visible = content.feed(text)
                        # 숨김 추론·태그·빈 텍스트 조각도 확인한 토큰 수는 즉시 전달한다.
                        if visible or confirmed:
                            yield ProviderDelta(text=visible, received_output_tokens=received)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise ProviderUnavailable(
                "MLX 추론 서버에 연결할 수 없습니다. 모델 서버를 먼저 실행해주세요.",
                request_started=response_received,
            ) from None
        except httpx.HTTPStatusError:
            raise ProviderUnavailable(
                "MLX 추론 서버가 요청을 처리하지 못했습니다. 서버 로그를 확인해주세요."
            ) from None
        except httpx.TimeoutException:
            raise ProviderUnavailable("모델 응답 제한 시간을 초과했습니다.") from None
        except httpx.HTTPError:
            raise ProviderUnavailable("MLX 서버와의 응답 연결이 중단되었습니다.") from None

        if usage is None:
            # 본문 길이나 이미 받은 조각 수로 과금하지 않는다. 호출자는 예약을 보류한다.
            raise ProviderUnavailable("MLX 서버의 최종 실제 토큰 사용량을 확인하지 못했습니다.")
        if remaining := content.finish():
            yield ProviderDelta(text=remaining, received_output_tokens=received)
        yield ProviderDelta(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            final=True,
            received_output_tokens=received,
            finish_reason=finish_reason,
            tool_calls=tool_calls.finish(),
        )

    async def count_tools(self, messages, options, tools) -> int:
        return await self.count_input(messages, options, tools=tools)

    def stream_tools(self, messages, options, tools):
        return self.stream(messages, options, tools=tools)
