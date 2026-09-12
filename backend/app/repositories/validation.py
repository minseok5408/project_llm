"""저장소와 서비스에서 공유하는 식별자·문자열·페이지 크기 검증."""

from uuid import UUID

from backend.app.repositories.errors import InvalidInput


def identifier(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidInput("식별자는 UUID여야 합니다.")
    return value


def required_text(value: str, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > maximum
        or "\x00" in value
    ):
        raise InvalidInput(f"{field} 값의 형식이나 길이가 올바르지 않습니다.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidInput(f"{field} 값의 문자 인코딩이 올바르지 않습니다.") from None
    return value.strip()


def optional_text(value: str | None, field: str) -> str | None:
    return None if value is None else required_text(value, 255, field)


def page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise InvalidInput("페이지 크기는 1부터 100까지의 정수여야 합니다.")
    return value
