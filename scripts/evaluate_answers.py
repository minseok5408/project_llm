#!/usr/bin/env python3
"""합성 답변 평가 자료 검증·로컬 모델 실행·사람 채점·기준선 비교를 수행한다."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.schemas import GenerationOptions  # noqa: E402
from backend.evaluation.runner import prepare_report, run_model  # noqa: E402
from backend.evaluation.schema import (  # noqa: E402
    DEFAULT_DATASET,
    load_dataset,
    local_model_url,
)
from backend.evaluation.scoring import assess, compare, review_template  # noqa: E402


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("평가 JSON 파일을 읽지 못했습니다.") from error
    if not isinstance(value, dict):
        raise ValueError("평가 파일은 JSON 객체여야 합니다.")
    return value


def write_json(path: Path, value: dict, *, replace: bool = False) -> None:
    """보고서는 0600으로 원자 저장하며 새 실행이 과거 결과를 덮어쓰지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".evaluation-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(serialized)
        if replace:
            os.replace(temporary, path)
        else:
            # 같은 이름으로 실행한 다른 평가가 있어도 결과를 덮어쓰지 않는다.
            os.link(temporary, path)
    except FileExistsError as error:
        raise ValueError("출력 파일이 이미 있습니다. 새 경로를 사용하세요.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command")
    for name in ("validate", "run"):
        child = subcommands.add_parser(name)
        child.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
        child.add_argument("--case", action="append")
        child.add_argument("--repeat", type=int, default=1)
        child.add_argument("--output", type=Path, required=name == "run")
        child.add_argument("--context-window", type=int, default=32768)
        child.add_argument("--max-tokens", type=int, default=512)
        child.add_argument("--thinking", action="store_true")
        child.add_argument("--timeout-seconds", type=float, default=120)
        if name == "run":
            child.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
            child.add_argument("--model", default="mlx-community/Qwen3.8-27B-4bit")
            child.add_argument("--review-template", type=Path)
    review = subcommands.add_parser("review-template")
    review.add_argument("--report", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    score = subcommands.add_parser("score")
    score.add_argument("--report", type=Path, required=True)
    score.add_argument("--review", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    diff = subcommands.add_parser("compare")
    diff.add_argument("--candidate", type=Path, required=True)
    diff.add_argument("--baseline", type=Path, required=True)
    diff.add_argument("--output", type=Path)
    return command


def configured(arguments) -> Settings:
    """평가 경로는 개발 DB·검색 공급자·서비스 사용량 장부에 접근하지 않는다."""
    return Settings(
        _env_file=None,
        llm_backend="mlx",
        llm_base_url=local_model_url(getattr(arguments, "base_url", "http://127.0.0.1:8080/v1")),
        llm_model_id=getattr(arguments, "model", "mlx-community/Qwen3.8-27B-4bit"),
        llm_context_window=arguments.context_window,
        llm_request_timeout_seconds=arguments.timeout_seconds,
        llm_max_history_chars=200_000,
        llm_compaction_trigger_ratio=0.75,
        llm_compaction_target_ratio=0.55,
        llm_compaction_keep_turns=4,
        llm_compaction_max_tokens=1024,
        database_enabled=False,
        database_url=None,
        migration_database_url=None,
        generation_worker_enabled=False,
        web_search_provider="disabled",
        web_search_api_key=None,
    )


def execute(arguments) -> int:
    if arguments.command in ("validate", "run"):
        dataset = load_dataset(arguments.dataset)
        settings = configured(arguments)
        options = GenerationOptions(thinking=arguments.thinking, max_tokens=arguments.max_tokens)
        report, prompts = prepare_report(
            dataset,
            arguments.dataset,
            settings,
            options,
            model=arguments.command == "run",
            selected=arguments.case,
            repeat=arguments.repeat,
        )
        output = arguments.output
        template_path = getattr(arguments, "review_template", None)
        if template_path and (
            template_path.exists() or template_path.resolve() == output.resolve()
        ):
            raise ValueError("채점 서식은 평가 보고서와 다른 새 파일이어야 합니다.")
        if output:
            write_json(output, report)
        if arguments.command == "run":

            def checkpoint(value):
                write_json(output, value, replace=True)

            report = asyncio.run(
                run_model(report, prompts, dataset, settings, options, checkpoint=checkpoint)
            )
            if template_path:
                write_json(template_path, review_template(report))
        print(
            json.dumps(
                {
                    "mode": report["mode"],
                    "cases": report["total_cases"],
                    "quality_status": report["quality_status"],
                    "notice": (
                        "자료 검증·실행 성공은 의미 품질 통과가 아닙니다. "
                        "사람 채점을 별도로 수행하세요."
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 1 if report["quality_status"] == "execution_failed" else 0
    if arguments.command == "review-template":
        write_json(arguments.output, review_template(read_json(arguments.report)))
        print("현재 보고서에 연결된 미채점 서식을 저장했습니다.")
        return 0
    if arguments.command == "score":
        report, review = read_json(arguments.report), read_json(arguments.review)
        assessment = assess(report, review)
        write_json(
            arguments.output,
            {
                "schema_version": 1,
                "report": report,
                "review": review,
                "assessment": assessment,
            },
        )
        result = assessment
    else:
        result = compare(read_json(arguments.candidate), read_json(arguments.baseline))
        if arguments.output:
            write_json(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] is True else 1 if result["passed"] is False else 2


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    cli = parser()
    parsed = cli.parse_args(arguments or ["validate"])
    try:
        return execute(parsed)
    except KeyboardInterrupt:
        print("평가를 중단했습니다. 완료된 사례는 지정 보고서에 보존됩니다.", file=sys.stderr)
        return 130
    except (OSError, ValidationError):
        # 환경 설정 검증 오류나 파일 오류의 원문·경로는 출력하지 않는다.
        print(
            "평가 입력·파일·설정을 처리하지 못했습니다. 형식과 새 출력 경로를 확인하세요.",
            file=sys.stderr,
        )
        return 2
    except ValueError as error:
        # 평가 계층은 사용자 자료를 넣지 않은 고정된 진단 문구만 제공한다.
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
