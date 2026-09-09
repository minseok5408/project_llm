#!/usr/bin/env python3
"""임시 로컬 PostgreSQL에서 마이그레이션과 백엔드 테스트를 실행하고 pytest 인자를 전달한다."""

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class CheckFailed(Exception):
    """테스트 사전 조건 확인이나 하위 프로세스 실행에 실패했음을 나타낸다."""


def main(pytest_args: list[str] | None = None) -> int:
    docker = shutil.which("docker")
    if docker is None:
        print(
            "Docker is unavailable. Install and start a local Docker engine first.", file=sys.stderr
        )
        return 1

    project = f"qwen-test-{uuid4().hex}"
    password = secrets.token_hex(24)
    database_url = ""
    environment = os.environ.copy()
    environment["TEST_POSTGRES_PASSWORD"] = password
    environment["LLM_BACKEND"] = "mock"
    environment.pop("PYTEST_ADDOPTS", None)
    compose = [
        docker,
        "compose",
        "--env-file",
        os.devnull,
        "--project-name",
        project,
        "--file",
        str(ROOT / "compose.test.yaml"),
    ]

    def redact(output: str) -> str:
        if database_url:
            output = output.replace(database_url, "<test database>")
        return output.replace(password, "<redacted>")

    def run(
        command: list[str],
        *,
        label: str,
        timeout: int = 60,
        show_output: bool = True,
    ) -> str:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            # 하위 프로세스의 부분 출력에도 접속 문자열이 포함될 수 있다.
            if error.output:
                output = error.output
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
                print(redact(output), end="" if output.endswith("\n") else "\n")
            raise CheckFailed(f"{label} timed out after {timeout} seconds.") from None
        except OSError as error:
            raise CheckFailed(f"{label} could not start: {redact(str(error))}") from None

        if (show_output or result.returncode) and result.stdout:
            print(redact(result.stdout), end="" if result.stdout.endswith("\n") else "\n")
        if result.returncode:
            raise CheckFailed(f"{label} failed (exit {result.returncode}).")
        return result.stdout.strip()

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, interrupt)
    cleanup_required = False
    exit_code = 0
    try:
        # 프로젝트 DB를 로컬에 유지하기 위해 원격 Docker 컨텍스트를 거부한다.
        docker_host = environment.get("DOCKER_HOST", "")
        if not docker_host or environment.get("DOCKER_CONTEXT"):
            context = run(
                [docker, "context", "show"],
                label="Docker context lookup",
                show_output=False,
            )
            docker_host = run(
                [docker, "context", "inspect", context, "--format", "{{.Endpoints.docker.Host}}"],
                label="Docker endpoint lookup",
                show_output=False,
            )
        if not docker_host.startswith("unix://"):
            raise CheckFailed(
                "Select a local Docker context with a Unix socket before running tests."
            )

        run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            label="Local Docker engine check",
            show_output=False,
            timeout=15,
        )
        run([docker, "compose", "version"], label="Docker Compose check", show_output=False)
        print("Starting an isolated PostgreSQL test database...", flush=True)
        cleanup_required = True
        run(
            [*compose, "up", "--detach", "--wait", "--wait-timeout", "90"],
            label="Test database startup",
            timeout=300,
        )
        address = run(
            [*compose, "port", "postgres", "5432"],
            label="Test database port lookup",
            show_output=False,
        )
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]+)", address)
        if match is None or not 1 <= int(match[1]) <= 65535:
            raise CheckFailed("Docker did not publish the test database on a local loopback port.")

        database_url = f"postgresql+asyncpg://qwen:{password}@{address}/qwen_test"
        for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "TEST_DATABASE_URL", "DB_URL"):
            environment[key] = database_url

        print("Checking migrations: upgrade, downgrade, upgrade, schema drift...", flush=True)
        for operation in (
            ["upgrade", "head"],
            ["downgrade", "base"],
            ["upgrade", "head"],
            ["check"],
        ):
            run(
                [sys.executable, "-m", "alembic", *operation],
                label=f"Alembic {' '.join(operation)}",
            )
        print("Running backend tests, including PostgreSQL integration tests...", flush=True)
        run(
            [sys.executable, "-m", "pytest", *(pytest_args or [])],
            label="Backend tests",
            timeout=300,
        )
    except KeyboardInterrupt:
        print("\nInterrupted; removing the temporary test database.", file=sys.stderr)
        exit_code = 130
    except CheckFailed as error:
        print(str(error), file=sys.stderr)
        exit_code = 1
    finally:
        if cleanup_required:
            # 이 프로젝트의 컨테이너를 제거할 때까지 반복된 중단 신호를 무시한다.
            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                run(
                    [*compose, "down", "--volumes", "--timeout", "10"],
                    label="Temporary database cleanup",
                    timeout=60,
                )
            except CheckFailed as error:
                print(str(error), file=sys.stderr)
                print(f"Cleanup may be needed for Docker project {project}.", file=sys.stderr)
                exit_code = 1
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
