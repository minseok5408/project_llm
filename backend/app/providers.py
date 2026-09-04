import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from backend.app.config import Settings
from backend.app.schemas import ChatMessage, GenerationOptions, ProviderStatus


class ProviderUnavailable(RuntimeError):
    """Raised when the configured inference provider cannot serve a request."""


@dataclass(frozen=True, slots=True)
class ProviderDelta:
    text: str


class ChatProvider(Protocol):
    async def status(self) -> ProviderStatus: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> AsyncIterator[ProviderDelta]: ...


class MockProvider:
    """Small deterministic provider used for UI development and automated tests."""

    def __init__(self, settings: Settings, delay_seconds: float = 0.015) -> None:
        self.settings = settings
        self.delay_seconds = delay_seconds

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            backend="mock",
            ready=True,
            model=self.settings.llm_model_id,
            detail="테스트용 추론 엔진 · 모델 다운로드 없이 실행 중",
        )

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> AsyncIterator[ProviderDelta]:
        latest = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        response = (
            "연결이 정상적으로 동작합니다. "
            f"방금 보낸 메시지는 ‘{latest[:80]}’입니다. "
            "실제 MLX 서버를 시작하면 같은 화면에서 Qwen3.8 27B의 응답이 스트리밍됩니다."
        )
        for token in response.split(" "):
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield ProviderDelta(text=f"{token} ")


class MlxServerProvider:
    """Adapter for the OpenAI-compatible API exposed by mlx-vlm.server."""

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
            async with httpx.AsyncClient(base_url=self.base_url, timeout=2) as client:
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

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> AsyncIterator[ProviderDelta]:
        thinking = options.thinking
        payload = {
            "model": self.settings.llm_model_id,
            "messages": [message.model_dump() for message in messages],
            "stream": True,
            "max_tokens": options.max_tokens,
            "temperature": 1.0 if thinking else 0.7,
            "top_p": 0.95 if thinking else 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0 if thinking else 1.5,
            "repetition_penalty": 1.0,
            "enable_thinking": thinking,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
            ) as client:
                async with client.stream("POST", "chat/completions", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue

                        raw_data = line[5:].strip()
                        if not raw_data or raw_data == "[DONE]":
                            continue

                        try:
                            chunk = json.loads(raw_data)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue

                        delta = choices[0].get("delta") or {}
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            yield ProviderDelta(text=text)
        except httpx.ConnectError as error:
            raise ProviderUnavailable(
                "MLX 추론 서버에 연결할 수 없습니다. 모델 서버를 먼저 실행해주세요."
            ) from error
        except httpx.HTTPStatusError as error:
            raise ProviderUnavailable(
                "MLX 추론 서버가 요청을 처리하지 못했습니다. 서버 로그를 확인해주세요."
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderUnavailable("모델 응답 제한 시간을 초과했습니다.") from error


def build_provider(settings: Settings) -> ChatProvider:
    if settings.llm_backend == "mock":
        return MockProvider(settings)
    return MlxServerProvider(settings)
