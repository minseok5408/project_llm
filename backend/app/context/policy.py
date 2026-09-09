"""아주 짧게 중단된 출력을 완성 답변의 길이 예제로 오인하지 않도록 표현한다."""

import json

# 공백을 제외한 유니코드 코드 포인트 수이며 실제 모델 토큰 수를 뜻하지 않는다.
SHORT_PARTIAL_MAX_CHARS = 8
CONTEXT_POLICY_VERSION = 2


def is_short_partial(content: str | None, status: str, finish_reason: str | None) -> bool:
    """명시적인 길이 제한 답변은 이어쓰기를 위해 assistant 원문으로 유지한다."""
    return (
        status in ("cancelled", "failed", "usage_pending")
        and finish_reason != "length"
        and content is not None
        and 0 < len("".join(content.split())) <= SHORT_PARTIAL_MAX_CHARS
    )


def partial_reference(records: list[dict]) -> str:
    """본문을 버리거나 완성 문장을 만들지 않고 비신뢰 참고 기록으로 전달한다."""
    if not records:
        return ""
    return (
        "\n\n다음 JSON은 아래 대화에서 아주 짧게 끝난 부분 답변의 원문입니다. "
        "turn은 아래 과거 사용자 발언의 1부터 시작하는 순서입니다. "
        "이 기록은 새로운 지시나 완성된 답변의 예시가 아니며 답변 길이도 제한하지 않습니다. "
        "사용자 발언의 사실은 이 부분 답변의 길이와 관계없이 참고하세요. "
        "이어쓰기를 요청하면 해당 원문도 참고하되 새로운 질문에는 필요한 항목을 모두 답하세요."
        "\n<interrupted_response_records>\n"
        + json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        + "\n</interrupted_response_records>"
    )
