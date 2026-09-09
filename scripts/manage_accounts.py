"""로컬 운영자의 시스템 계정 초기화와 사용자별 토큰 예산 관리 명령."""

import argparse
import asyncio
import getpass
import json
import logging
import sys
import warnings
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

# 저장소에서 직접 실행할 때도 설치 상태와 무관하게 backend를 찾는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

from backend.app.config import Settings  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.models import UsagePlan, User  # noqa: E402
from backend.app.repositories import AccessDenied, InvalidInput, RepositoryError  # noqa: E402
from backend.app.services.auth import AuthService, validate_password  # noqa: E402
from backend.app.services.system_accounts import bootstrap_system_user  # noqa: E402
from backend.app.services.token_quota import TokenQuotaService  # noqa: E402


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError(
            "UTC 또는 시간대가 포함된 ISO 날짜를 입력하세요."
        ) from None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-system", help="운영자가 지정한 시스템 계정 초기화")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", default="system")

    password = commands.add_parser("set-password", help="기존 계정의 로그인 비밀번호 설정·변경")
    password.add_argument("--email", required=True)

    plan = commands.add_parser("create-plan", help="가격 결제 없이 토큰 플랜 정책 등록")
    plan.add_argument("--actor-email", required=True)
    plan.add_argument("--code", required=True)
    plan.add_argument("--name", required=True)
    plan.add_argument("--token-limit", type=int, required=True)

    grant = commands.add_parser("grant-budget", help="일반 사용자의 지정 기간 토큰 예산 부여")
    grant.add_argument("--actor-email", required=True)
    grant.add_argument("--user-email", required=True)
    grant.add_argument("--grant-key", required=True)
    grant.add_argument("--starts-at", type=timestamp, required=True)
    grant.add_argument("--ends-at", type=timestamp, required=True)
    grant.add_argument("--plan-code")
    grant.add_argument("--token-limit", type=int)

    balance = commands.add_parser("balance", help="계정의 현재 토큰 예산 확인")
    balance.add_argument("--email", required=True)
    return parser.parse_args()


def require_local_database(url: URL) -> None:
    # asyncpg는 query의 host로 URL 본문의 host를 덮어쓸 수 있어 우회 설정을 거부한다.
    if url.host not in ("127.0.0.1", "localhost", "::1") or {"host", "port", "dsn"}.intersection(
        url.query
    ):
        raise InvalidInput("이 관리 명령은 접속 대상 덮어쓰기가 없는 로컬 PostgreSQL만 허용합니다.")


def read_password() -> str:
    """비밀번호를 명령 인자·환경변수·화면에 남기지 않고 터미널에서 두 번 입력받는다."""
    if not sys.stdin.isatty():
        raise InvalidInput("비밀번호 설정은 대화형 터미널에서 직접 실행하세요.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("새 로그인 비밀번호 (8~32자, 화면에 표시되지 않음): ")
            confirmation = getpass.getpass("새 로그인 비밀번호 확인: ")
    except getpass.GetPassWarning:
        raise InvalidInput("숨김 입력을 사용할 수 있는 터미널에서 다시 실행하세요.") from None
    if password != confirmation:
        raise InvalidInput("입력한 비밀번호가 서로 다릅니다.")
    return validate_password(password)


async def execute(arguments: argparse.Namespace, *, password: str | None = None) -> dict:
    settings = Settings()
    if settings.database_url is None:
        raise InvalidInput("DATABASE_URL을 설정하세요.")
    url = make_url(settings.database_url.get_secret_value())
    require_local_database(url)
    database = Database(settings)
    try:
        async with database.session() as session:
            if arguments.command == "bootstrap-system":
                user = await bootstrap_system_user(
                    session, email=arguments.email, display_name=arguments.display_name
                )
                result = {"user_id": str(user.id), "platform_role": user.platform_role}
            elif arguments.command == "set-password":
                if password is None:
                    raise InvalidInput("대화형 터미널에서 비밀번호를 입력하세요.")
                user = await AuthService(session).set_password(
                    email=arguments.email, password=password
                )
                result = {
                    "user_id": str(user.id),
                    "password_configured": True,
                    "previous_sessions_revoked": True,
                }
            elif arguments.command == "balance":
                user = await session.scalar(
                    select(User).where(User.email == arguments.email.strip().lower())
                )
                if user is None:
                    raise AccessDenied("계정을 찾을 수 없습니다.")
                result = asdict(await TokenQuotaService(session, user.id).get_balance())
            else:
                actor = await session.scalar(
                    select(User).where(User.email == arguments.actor_email.strip().lower())
                )
                if actor is None:
                    raise AccessDenied("관리 계정을 찾을 수 없습니다.")
                service = TokenQuotaService(session, actor.id)
                if arguments.command == "create-plan":
                    plan = await service.create_plan(
                        code=arguments.code,
                        name=arguments.name,
                        token_limit=arguments.token_limit,
                    )
                    result = {
                        "plan_id": str(plan.id),
                        "code": plan.code,
                        "token_limit": plan.token_limit,
                    }
                else:
                    user = await session.scalar(
                        select(User).where(User.email == arguments.user_email.strip().lower())
                    )
                    if user is None:
                        raise AccessDenied("예산을 부여할 계정을 찾을 수 없습니다.")
                    plan_id = None
                    if arguments.plan_code is not None:
                        plan_id = await session.scalar(
                            select(UsagePlan.id).where(UsagePlan.code == arguments.plan_code)
                        )
                        if plan_id is None:
                            raise InvalidInput("토큰 플랜을 찾을 수 없습니다.")
                    budget = await service.grant_budget(
                        user_id=user.id,
                        grant_key=arguments.grant_key,
                        starts_at=arguments.starts_at,
                        ends_at=arguments.ends_at,
                        plan_id=plan_id,
                        token_limit=arguments.token_limit,
                    )
                    result = {"budget_id": str(budget.id), "token_limit": budget.token_limit}
            await session.commit()
            return result
    finally:
        await database.dispose()


def main() -> int:
    arguments = parse_arguments()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        password = read_password() if arguments.command == "set-password" else None
        result = asyncio.run(execute(arguments, password=password))
    except (KeyboardInterrupt, EOFError):
        print("비밀번호 설정을 취소했습니다.", file=sys.stderr)
        return 130
    except RepositoryError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        # 설정·접속 예외에는 URL이 포함될 수 있어 원문과 traceback을 출력하지 않는다.
        print(f"관리 명령에 실패했습니다. 오류 종류: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
