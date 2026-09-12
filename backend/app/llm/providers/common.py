"""입력 계산과 생성 요청에 같은 모델 메시지 형식을 적용한다."""

from collections.abc import Sequence

from backend.app.context.language import answer_language_reminder
from backend.app.schemas import ChatMessage


def _normalized_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """MLX의 입력 토큰 계산 API와 생성 API에 동일한 시스템 메시지를 전달한다."""
    instructions = [message.content for message in messages if message.role == "system"]
    conversation = [message.model_dump() for message in messages if message.role != "system"]
    # 저장 원문과 검색 계획에는 안내를 섞지 않고 토큰 계산·추론 직전의 복사본에만 넣는다.
    # 요약·검색 계획처럼 별도 시스템 지침을 쓰는 내부 호출에는 답변 언어를 강제하지 않는다.
    if (
        messages
        and messages[0].role == "system"
        and conversation
        and conversation[-1]["role"] == "user"
    ):
        reminder = answer_language_reminder(messages[0].content)
        if reminder:
            conversation[-1]["content"] += "\n\n[Response language for this turn]\n" + reminder
    if instructions:
        conversation.insert(0, {"role": "system", "content": "\n\n".join(instructions)})
    return conversation
