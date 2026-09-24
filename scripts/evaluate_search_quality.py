#!/usr/bin/env python3
"""검색 판단·검색어·실제 근거·답변을 평가하고 공통 사람 채점 계약을 재사용한다.

인자 없는 실행은 외부 요청 없는 자료 검증이다. run은 이미 실행 중인 loopback
모델과 고정 검색 응답을 사용한다. --live-search --case는 지정된 공개 질문만
설정된 검색 공급자에 전송한다. DB·사용자 사용량 장부·서버 시작은 사용하지 않는다.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.schemas import GenerationOptions  # noqa: E402
from backend.app.tools.web_search.provider import build_search_provider  # noqa: E402
from backend.evaluation.scoring import review_template  # noqa: E402
from backend.evaluation.search_runner import prepare_search_report, run_search_model  # noqa: E402
from backend.evaluation.search_schema import (  # noqa: E402
    DEFAULT_SEARCH_DATASET,
    load_search_dataset,
)
from scripts import evaluate_answers  # noqa: E402


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command")
    for name in ("validate", "run"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", type=Path, default=DEFAULT_SEARCH_DATASET)
        command.add_argument("--case", action="append")
        command.add_argument("--output", type=Path, required=name == "run")
        command.add_argument("--repeat", type=int, default=1)
        command.add_argument("--context-window", type=int, default=32768)
        command.add_argument("--max-tokens", type=int, default=512)
        command.add_argument("--thinking", action="store_true")
        command.add_argument("--timeout-seconds", type=float, default=120)
        if name == "run":
            command.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
            command.add_argument("--model", default="mlx-community/Qwen3.8-27B-4bit")
            command.add_argument("--live-search", action="store_true")
            command.add_argument("--review-template", type=Path)
    template = commands.add_parser("review-template")
    template.add_argument("--report", type=Path, required=True)
    template.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("score")
    score.add_argument("--report", type=Path, required=True)
    score.add_argument("--review", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--output", type=Path)
    return cli


def execute(arguments) -> int:
    if arguments.command not in ("validate", "run"):
        return evaluate_answers.execute(arguments)
    dataset = load_search_dataset(arguments.dataset)
    settings = evaluate_answers.configured(arguments)
    live = getattr(arguments, "live_search", False)
    if live:
        configured = Settings()
        settings = settings.model_copy(
            update={
                name: getattr(configured, name)
                for name in Settings.model_fields
                if name.startswith("web_search_")
            }
        )
    options = GenerationOptions(thinking=arguments.thinking, max_tokens=arguments.max_tokens)
    report = prepare_search_report(
        dataset,
        arguments.dataset,
        settings,
        options,
        model=arguments.command == "run",
        live_search=live,
        selected=arguments.case,
        repeat=arguments.repeat,
    )
    output = arguments.output
    template = getattr(arguments, "review_template", None)
    if template and (template.exists() or template.resolve() == output.resolve()):
        raise ValueError("채점 서식은 평가 보고서와 다른 새 파일이어야 합니다.")
    search_provider = build_search_provider(settings) if live else None
    if search_provider is not None and not search_provider.configured:
        raise ValueError("실제 검색 공급자와 인증 설정을 확인해야 합니다.")
    if output:
        evaluate_answers.write_json(output, report)
    if arguments.command == "run":

        def checkpoint(value):
            evaluate_answers.write_json(output, value, replace=True)

        report = asyncio.run(
            run_search_model(
                report,
                dataset,
                settings,
                options,
                search_provider=search_provider,
                checkpoint=checkpoint,
            )
        )
        if template:
            evaluate_answers.write_json(template, review_template(report))
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "search_scope": report["search_scope"],
                "cases": report["total_cases"],
                "quality_status": report["quality_status"],
                "notice": (
                    "진단 통과는 의미 품질 통과가 아닙니다. "
                    "보존한 검색 근거와 답변을 함께 채점하세요."
                ),
            },
            ensure_ascii=False,
        )
    )
    return 1 if report["quality_status"] == "execution_failed" else 0


def main(argv: list[str] | None = None) -> int:
    parsed = parser().parse_args((sys.argv[1:] if argv is None else argv) or ["validate"])
    try:
        return execute(parsed)
    except KeyboardInterrupt:
        print(
            "평가를 중단했습니다. 완료된 사례와 확인된 검색 사용량은 보고서에 보존됩니다.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValidationError):
        print("평가 입력·설정·출력 경로를 확인해야 합니다.", file=sys.stderr)
        return 2
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
