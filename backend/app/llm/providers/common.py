"""입력 계산과 생성 요청에 같은 모델 메시지 형식을 적용한다."""

from collections.abc import Sequence

from backend.app.schemas import ChatMessage


def _normalized_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """MLX의 입력 토큰 계산 API와 생성 API에 동일한 시스템 메시지를 전달한다."""
    instructions = [message.content for message in messages if message.role == "system"]
    conversation = [message.model_dump() for message in messages if message.role != "system"]
    if instructions:
        conversation.insert(0, {"role": "system", "content": "\n\n".join(instructions)})
    return conversation
