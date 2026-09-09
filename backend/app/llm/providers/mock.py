"""모델 다운로드 없이 결정적인 스트림과 사용량을 제공한다."""

import asyncio
import re
from collections.abc import AsyncIterator, Sequence

from backend.app.config import Settings
from backend.app.llm.protocol import ProviderDelta
from backend.app.llm.providers.common import _normalized_messages
from backend.app.schemas import ChatMessage, GenerationOptions, ProviderStatus


class MockProvider:
    """UI 개발과 자동화 테스트에서 동일한 입력에 동일한 응답을 제공하는 간단한 공급자."""

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

    async def count_input(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> int:
        # 모의 엔진은 공백으로 구분한 단어와 역할·경계 표식을 각각 한 토큰으로 소비한다.
        tokens = ["<|begin|>"]
        for message in _normalized_messages(messages):
            tokens.extend((message["role"], *message["content"].split(), "<|end|>"))
        tokens.extend(("assistant", "<think>" if options.thinking else "<answer>"))
        return len(tokens)

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
        response_tokens = re.findall(r"\S+\s*", response)
        tokens = response_tokens[: options.max_tokens]
        for received, token in enumerate(tokens, start=1):
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield ProviderDelta(text=token, received_output_tokens=received)
        yield ProviderDelta(
            input_tokens=await self.count_input(messages, options),
            output_tokens=len(tokens),
            final=True,
            received_output_tokens=len(tokens),
            finish_reason="length" if len(response_tokens) > len(tokens) else "stop",
        )
