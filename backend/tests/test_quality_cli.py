"""임시 모의 자료로 평가 CLI의 실행·채점·비교 연결과 종료 코드를 확인한다."""

import json
import socket

import pytest

from backend.app.llm.protocol import ProviderDelta
from backend.evaluation import runner
from backend.evaluation.schema import load_dataset
from scripts import evaluate_answers as cli


class SyntheticProvider:
    """CLI 연결 검사용 응답만 반환하며 실제 품질 평가를 대신하지 않는다."""

    async def count_input(self, messages, options):
        return 100

    async def stream(self, messages, options):
        yield ProviderDelta(text="CLI 연결 검사용 모의 답변입니다.", received_output_tokens=3)
        yield ProviderDelta(input_tokens=100, output_tokens=3, final=True, finish_reason="stop")


@pytest.fixture(autouse=True)
def offline_cli(monkeypatch):
    """전체 명령 연결을 검사하되 모델·네트워크 접속은 허용하지 않는다."""

    def forbidden(*args, **kwargs):
        pytest.fail("CLI 계약 검사에서 실제 모델 또는 네트워크에 접근했습니다.")

    async def synthetic_run(*args, **kwargs):
        return await runner.run_model(*args, **kwargs, provider=SyntheticProvider())

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(runner, "MlxServerProvider", forbidden)
    monkeypatch.setattr(runner, "implementation_metadata", lambda: {"commit": "synthetic-test"})
    monkeypatch.setattr(cli, "run_model", synthetic_run)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def create_artifacts(tmp_path, name, *, contract=False, subset=False):
    """고정 질문셋과 가짜 공급자로 임시 보고서·미채점 서식만 만든다."""
    report = tmp_path / f"{name}-report.json"
    review = tmp_path / f"{name}-review.json"
    scored = tmp_path / f"{name}-scored.json"
    arguments = ["validate" if contract else "run", "--output", str(report)]
    if subset:
        arguments.extend(["--case", load_dataset().cases[0].id])
    assert cli.main(arguments) == 0
    assert (
        cli.main(
            [
                "review-template",
                "--report",
                str(report),
                "--output",
                str(review),
            ]
        )
        == 0
    )
    return report, review, scored


def fill_synthetic_review(path, *, failure=None):
    """사람 채점 품질이 아닌 명령 연결을 검사하기 위한 임시 점수를 채운다."""
    review = read_json(path)
    review.update(reviewer="CLI 계약 테스트", reviewed_at="2026-09-22T12:00:00+09:00")
    for case in review["cases"]:
        for criterion in case["criteria"]:
            criterion.update(
                score=2,
                notes="CLI 계약 검사용 모의 점수이며 실제 의미 평가가 아닙니다.",
            )
    if failure == "absolute":
        review["cases"][0]["criteria"][0]["score"] = 0
    elif failure == "regression":
        for case in review["cases"][:6]:
            case["criteria"][0]["score"] = 1
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")


def score(artifacts):
    report, review, scored = artifacts
    return cli.main(
        [
            "score",
            "--report",
            str(report),
            "--review",
            str(review),
            "--output",
            str(scored),
        ]
    )


def compare(candidate, reference, output):
    return cli.main(
        [
            "compare",
            "--candidate",
            str(candidate[2]),
            "--baseline",
            str(reference[2]),
            "--output",
            str(output),
        ]
    )


@pytest.mark.parametrize("contract", [True, False])
def test_unexecuted_or_unreviewed_reports_return_incomplete_exit_code(tmp_path, contract):
    artifacts = create_artifacts(tmp_path, "incomplete", contract=contract)
    if contract:
        fill_synthetic_review(artifacts[1])
    assert score(artifacts) == 2
    assessment = read_json(artifacts[2])["assessment"]
    assert assessment["status"] == ("not_run" if contract else "pending_review")
    assert assessment["passed"] is None
    assert assessment["overall_score"] is None


def test_completed_cli_chain_compares_two_qualified_synthetic_reports(tmp_path):
    reference = create_artifacts(tmp_path, "synthetic-reference")
    candidate = create_artifacts(tmp_path, "synthetic-candidate")
    for artifacts in (reference, candidate):
        fill_synthetic_review(artifacts[1])
        assert score(artifacts) == 0
        assessed = read_json(artifacts[2])
        assert assessed["assessment"]["scope"] == "full"
        assert assessed["report"]["total_cases"] == 24
    output = tmp_path / "synthetic-comparison.json"
    assert compare(candidate, reference, output) == 0
    result = read_json(output)
    assert result["passed"] is True
    assert result["overall_delta"] == 0
    assert all(case["delta"] == 0 for case in result["case_deltas"])


@pytest.mark.parametrize("failure", ["absolute", "regression"])
def test_cli_comparison_fails_quality_or_regression_even_after_complete_review(tmp_path, failure):
    reference = create_artifacts(tmp_path, "synthetic-reference")
    candidate = create_artifacts(tmp_path, "synthetic-candidate")
    fill_synthetic_review(reference[1])
    fill_synthetic_review(candidate[1], failure=failure)
    assert score(reference) == 0
    assert score(candidate) == (1 if failure == "absolute" else 0)
    output = tmp_path / "failed-comparison.json"
    assert compare(candidate, reference, output) == 1
    result = read_json(output)
    assert result["passed"] is False
    assert result["failures"]
    if failure == "regression":
        assert result["overall_delta"] < -0.02


def test_modified_report_rejects_its_old_review_without_writing_a_score(tmp_path, capsys):
    artifacts = create_artifacts(tmp_path, "stale-review")
    fill_synthetic_review(artifacts[1])
    report = read_json(artifacts[0])
    report["configuration"]["model"] = "다른 모델을 나타내는 비공개 검사 표식"
    artifacts[0].write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert score(artifacts) == 2
    assert not artifacts[2].exists()
    error = capsys.readouterr().err
    assert "현재 평가 보고서와 일치하지 않습니다" in error
    assert "비공개 검사 표식" not in error


def test_identical_subsets_cannot_be_promoted_to_a_complete_comparison(tmp_path, capsys):
    reference = create_artifacts(tmp_path, "synthetic-subset-reference", subset=True)
    candidate = create_artifacts(tmp_path, "synthetic-subset-candidate", subset=True)
    for artifacts in (reference, candidate):
        fill_synthetic_review(artifacts[1])
        assert score(artifacts) == 0
        assert read_json(artifacts[2])["assessment"]["scope"] == "subset"
    output = tmp_path / "invalid-subset-comparison.json"
    capsys.readouterr()
    assert compare(candidate, reference, output) == 2
    assert not output.exists()
    assert "전체 사례" in capsys.readouterr().err


def test_score_and_compare_do_not_overwrite_existing_outputs(tmp_path, capsys):
    reference = create_artifacts(tmp_path, "synthetic-reference")
    candidate = create_artifacts(tmp_path, "synthetic-candidate")
    for artifacts in (reference, candidate):
        fill_synthetic_review(artifacts[1])
        assert score(artifacts) == 0
    original_score = candidate[2].read_bytes()
    capsys.readouterr()
    assert score(candidate) == 2
    assert candidate[2].read_bytes() == original_score
    assert "이미 있습니다" in capsys.readouterr().err
    output = tmp_path / "existing-comparison.json"
    output.write_text("기존 비교 보고서", encoding="utf-8")
    assert compare(candidate, reference, output) == 2
    assert output.read_text(encoding="utf-8") == "기존 비교 보고서"
    assert "이미 있습니다" in capsys.readouterr().err
