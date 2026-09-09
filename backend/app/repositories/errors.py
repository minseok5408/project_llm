"""입력값이나 SQL 원문을 포함하지 않는 저장소 예외."""


class RepositoryError(Exception):
    """호출자가 처리할 수 있는 저장소 오류의 공통 기반."""


class InvalidInput(RepositoryError):
    """저장소 입력 형식이나 범위가 올바르지 않다."""


class AccessDenied(RepositoryError):
    """객체가 없거나 현재 사용자에게 접근 권한이 없다."""


class Conflict(RepositoryError):
    """중복 값 또는 데이터 제약조건으로 쓰기를 완료할 수 없다."""


class RepositoryUnavailable(RepositoryError):
    """데이터베이스 작업을 완료할 수 없다."""
