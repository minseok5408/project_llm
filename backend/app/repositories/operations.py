"""데이터 작업의 오류 변환과 비밀값 없는 공통 관측 기록."""

import json
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app.repositories.errors import Conflict, RepositoryError, RepositoryUnavailable

logger = logging.getLogger(__name__)


def logged[**P, T](operation: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """고정된 작업명과 결과, 소요시간, UUID만 기록하고 DB 예외를 안전하게 바꾼다."""

    @wraps(operation)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        started = perf_counter()
        result = "error"
        try:
            value = await operation(*args, **kwargs)
            result = "ok"
            return value
        except IntegrityError:
            result = "conflict"
            raise Conflict("데이터 제약조건으로 작업을 완료할 수 없습니다.") from None
        except SQLAlchemyError:
            result = "unavailable"
            raise RepositoryUnavailable("데이터베이스 작업을 완료할 수 없습니다.") from None
        except RepositoryError as error:
            result = type(error).__name__
            raise
        finally:
            record: dict[str, str | float] = {
                "event": "repository_operation",
                "operation": operation.__name__,
                "result": result,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            }
            workspace_id = kwargs.get("workspace_id")
            if workspace_id is None and len(args) > 1:
                workspace_id = args[1]
            if isinstance(workspace_id, UUID):
                record["workspace_id"] = str(workspace_id)
            logger.info(json.dumps(record, separators=(",", ":")))

    return wrapped
