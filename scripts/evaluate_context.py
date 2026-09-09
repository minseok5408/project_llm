#!/usr/bin/env python3
"""기본은 오프라인 계약 검사이며 --mode model에서만 기존 로컬 모델을 호출한다."""

import argparse
import asyncio
import ipaddress
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.llm.providers.mlx import MlxServerProvider  # noqa: E402
from backend.context_evaluation import evaluate  # noqa: E402
from backend.evaluation_cases import cases  # noqa: E402


def local_model_url(value: str) -> str:
    """실제 평가 요청은 인증 정보 없는 loopback HTTP 주소에만 허용한다."""
    try:
        parsed = urlsplit(value)
        loopback = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname or "").is_loopback
        )
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        raise argparse.ArgumentTypeError(
            "인증 정보 없는 loopback HTTP 모델 주소가 필요합니다."
        ) from None
    if (
        parsed.scheme != "http"
        or not loopback
        or not valid_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("인증 정보 없는 loopback HTTP 모델 주소가 필요합니다.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("contract", "model"), default="contract")
    parser.add_argument("--case", action="append", choices=[case.name for case in cases()])
    parser.add_argument("--base-url", type=local_model_url, default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="mlx-community/Qwen3.8-27B-4bit")
    parser.add_argument("--context-window", type=int, default=4096)
    parser.add_argument("--min-recall", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if not 1024 <= arguments.context_window <= 32768:
        parser.error("--context-window는 1024부터 32768 사이여야 합니다.")
    if not 0 <= arguments.min_recall <= 1:
        parser.error("--min-recall은 0부터 1 사이여야 합니다.")
    configured = Settings(
        _env_file=None,
        llm_backend="mlx" if arguments.mode == "model" else "mock",
        llm_base_url=arguments.base_url,
        llm_model_id=arguments.model,
        # 오프라인 검사는 작은 창에서 반복 압축을 재현한다. 실제 모델은 지정값을 쓴다.
        llm_context_window=arguments.context_window if arguments.mode == "model" else 1024,
        llm_compaction_max_tokens=512 if arguments.mode == "model" else 128,
        llm_compaction_trigger_ratio=0.75,
        llm_compaction_target_ratio=0.55,
        llm_compaction_keep_turns=4,
        llm_max_history_chars=200_000,
        llm_request_timeout_seconds=120,
        database_enabled=False,
        database_url=None,
        migration_database_url=None,
        generation_worker_enabled=False,
    )
    provider = MlxServerProvider(configured) if arguments.mode == "model" else None
    selected = [case for case in cases() if not arguments.case or case.name in arguments.case]
    report = asyncio.run(
        evaluate(selected, configured, provider=provider, minimum_recall=arguments.min_recall)
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
