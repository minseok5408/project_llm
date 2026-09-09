"""모델 공급자의 상태·스트림·확정 사용량 계약을 정의한다."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from backend.app.schemas import ChatMessage, GenerationOptions, ProviderStatus


class ProviderUnavailable(RuntimeError):
    """설정된 추론 공급자가 요청을 처리할 수 없을 때 발생하는 예외."""

    def __init__(self, message: str, *, request_started: bool = True) -> None:
        super().__init__(message)
        # 생성 요청이 전혀 시작되지 않았음이 확실한 경우에만 예약을 환급할 수 있다.
        self.request_started = request_started


@dataclass(frozen=True, slots=True)
class ProviderDelta:
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    final: bool = False
    received_output_tokens: int | None = None
    finish_reason: str | None = None


class ChatProvider(Protocol):
    async def status(self) -> ProviderStatus: ...

    async def count_input(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> int: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        options: GenerationOptions,
    ) -> AsyncIterator[ProviderDelta]: ...
