"""대화 목록의 정렬 위치를 담는 검증된 커서를 변환한다."""

import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from backend.app.models import Conversation
from backend.app.repositories.errors import InvalidInput


def decode_cursor(value: str) -> tuple[datetime, UUID]:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise InvalidInput("대화 목록 커서가 올바르지 않습니다.")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        parts = json.loads(decoded)
        if not isinstance(parts, list) or len(parts) != 2:
            raise ValueError
        timestamp = datetime.fromisoformat(parts[0])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(UTC), UUID(parts[1])
    except (ValueError, TypeError, AttributeError, OverflowError, binascii.Error, UnicodeError):
        raise InvalidInput("대화 목록 커서가 올바르지 않습니다.") from None


def encode_cursor(conversation: Conversation) -> str:
    position = [conversation.last_message_at.astimezone(UTC).isoformat(), str(conversation.id)]
    return base64.urlsafe_b64encode(json.dumps(position).encode()).decode().rstrip("=")
