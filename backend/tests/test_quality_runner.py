"""평가 실행의 격리·입력 누출 방지·실패 기록과 결과 파일 보존을 검증한다."""

import asyncio
import copy
import json
import socket
import stat

import pytest

from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable
from backend.app.schemas import GenerationOptions
from backend.evaluation import runner
from backend.evaluation.schema import DEFAULT_DATASET, load_dataset, local_model_url
from scripts import evaluate_answers as cli


class ScriptedProvider:
    """정해 둔 스트림과 예외로 실행 계약만 확인하며 실제 모델을 호출하지 않는다."""

    def __init__(self, events, *, counted=100):
        self.events = events
        self.counted = counted
        self.count_calls = 0
        self.stream_calls = 0
        self.closed = False

    async def count_input(self, messages, options):
        self.count_calls += 1
        return self.counted

    async def stream(self, messages, options):
        self.stream_calls += 1
        try:
            for event in self.events:
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            self.closed = True


@pytest.fixture(autouse=True)
def isolated_implementation_metadata(monkeypatch):
    """실행 계약 검사에서는 작업 트리 상태나 설치 패키지에 의존하지 않는다."""
    monkeypatch.setattr(runner, "implementation_metadata", lambda: {"commit": "test"})


@pytest.fixture
def evaluation_inputs():
    dataset = load_dataset()
    settings = cli.configured(cli.parser().parse_args(["validate"]))
    options = GenerationOptions(max_tokens=32, thinking=False)
    report, prompts = runner.prepare_report(
        dataset,
        DEFAULT_DATASET,
        settings,
        options,
        model=True,
        selected=[dataset.cases[0].id],
    )
    return dataset, settings, options, report, prompts


async def run_script(evaluation_inputs, events, *, counted=100):
    dataset, settings, options, report, prompts = evaluation_inputs
    provider = ScriptedProvider(events, counted=counted)
    await runner.run_model(report, prompts, dataset, settings, options, provider=provider)
    return report, report["cases"][0], provider


def final_delta(**changes):
    return ProviderDelta(
        **{
            "final": True,
            "input_tokens": 100,
            "output_tokens": 3,
            "finish_reason": "stop",
            **changes,
        }
    )


def test_default_cli_validates_without_network_or_model(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("기본 자료 검증에서 네트워크 또는 모델 실행을 시도했습니다.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(runner, "MlxServerProvider", forbidden)
    monkeypatch.setattr(cli, "run_model", forbidden)
    captured = []
    prepare = cli.prepare_report

    def capture(*args, **kwargs):
        report, prompts = prepare(*args, **kwargs)
        captured.append(report)
        return report, prompts

    monkeypatch.setattr(cli, "prepare_report", capture)
    assert cli.main([]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "contract"
    assert summary["quality_status"] == "not_run"
    assert summary["cases"] == 24
    assert captured[0]["configuration"]["model"] is None
    assert captured[0]["completed_cases"] == 0
    assert all(item["answer"] is None for item in captured[0]["cases"])
    assert all(item["execution"]["status"] == "not_run" for item in captured[0]["cases"])


def test_scoring_answers_and_expected_language_never_reach_model_messages():
    for case in load_dataset().cases:
        original = [message.model_dump() for message in runner.build_messages(case)]
        altered = case.model_copy(deep=True)
        altered.expected_language = "PRIVATE_LANGUAGE_38a1"
        for criterion in altered.criteria:
            criterion.description = "PRIVATE_RUBRIC_1e92"
            criterion.score_anchors = {str(score): "PRIVATE_ANSWER_07bb" for score in range(3)}
        for check in altered.lexical_checks:
            check.terms = ["PRIVATE_LEXICAL_95d0"]
        actual = [message.model_dump() for message in runner.build_messages(altered)]
        assert actual == original, case.id
        serialized = json.dumps(actual, ensure_ascii=False)
        assert "PRIVATE_" not in serialized
        assert any(case.question in message["content"] for message in actual)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://example.com:8080/v1",
        "http://192.168.0.2:8080/v1",
        "http://0.0.0.0:8080/v1",
        "http://[::]:8080/v1",
        "http://user:password@127.0.0.1:8080/v1",
        "http://user@localhost:8080/v1",
        "http://127.0.0.1:8080/v1?secret=fixture",
        "http://localhost:8080/v1#fixture",
        "http://127.0.0.1:70000/v1",
        "http://[broken/v1",
    ],
)
def test_model_address_rejects_remote_lan_credentials_and_suffixes(url):
    with pytest.raises(ValueError, match="loopback"):
        local_model_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/v1",
        "http://localhost:8080/v1",
        "http://[::1]:8080/v1",
    ],
)
def test_model_address_allows_only_local_http_examples(url):
    assert local_model_url(url) == url


async def test_completed_generation_records_usage_without_claiming_quality(evaluation_inputs):
    report, result, provider = await run_script(
        evaluation_inputs,
        [
            ProviderDelta(text="사실 여부를 사람이 확인할 답변.", received_output_tokens=2),
            final_delta(),
        ],
    )
    assert report["quality_status"] == "pending_review"
    assert report["completed_cases"] == 1
    assert result["execution"]["status"] == "completed"
    assert result["execution"]["counted_input_tokens"] == 100
    assert result["execution"]["input_tokens"] == 100
    assert result["execution"]["output_tokens"] == 3
    assert result["execution"]["usage_status"] == "confirmed"
    assert result["execution"]["received_output_tokens"] == 2
    assert result["execution"]["first_token_seconds"] is not None
    assert result["execution"]["elapsed_seconds"] >= 0
    assert len(result["answer_sha256"]) == 64
    assert provider.closed


@pytest.mark.parametrize(
    "delta",
    [
        final_delta(input_tokens=None),
        final_delta(input_tokens=True),
        final_delta(input_tokens=0),
        final_delta(input_tokens=101),
        final_delta(output_tokens=None),
        final_delta(output_tokens=True),
        final_delta(output_tokens=-1),
        final_delta(output_tokens=33),
    ],
)
async def test_invalid_or_inconsistent_usage_fails_generation(evaluation_inputs, delta):
    report, result, _ = await run_script(
        evaluation_inputs,
        [
            ProviderDelta(text="일부 답변"),
            delta,
        ],
    )
    assert report["quality_status"] == "execution_failed"
    assert result["execution"]["status"] == "failed"
    assert result["execution"]["error"] == "invalid_final_usage"
    assert result["answer"] == "일부 답변"
    assert result["execution"]["usage_status"] == "invalid"
    assert result["execution"]["input_tokens"] is None
    assert result["execution"]["output_tokens"] is None
    for name in ("input_tokens", "output_tokens"):
        reported = getattr(delta, name)
        expected = reported if type(reported) is int and reported >= 0 else None
        assert result["execution"][f"reported_{name}"] == expected


@pytest.mark.parametrize(
    ("events", "reason"),
    [
        ([ProviderDelta(text="답변만 있고 정산 없음")], "missing_final_usage"),
        (
            [ProviderDelta(text="잘린 답변"), final_delta(finish_reason="length")],
            "incomplete_answer",
        ),
        ([final_delta()], "empty_answer"),
        ([ProviderDelta(text=" \n\t"), final_delta()], "empty_answer"),
        (
            [ProviderDelta(text="답변"), final_delta(), ProviderDelta(text="추가")],
            "data_after_final",
        ),
    ],
)
async def test_missing_usage_truncation_empty_and_post_final_data_fail(
    evaluation_inputs,
    events,
    reason,
):
    report, result, provider = await run_script(evaluation_inputs, events)
    assert report["quality_status"] == "execution_failed"
    assert result["execution"]["error"] == reason
    assert provider.closed


@pytest.mark.parametrize("after_final", [False, True])
async def test_provider_failure_preserves_partial_answer_and_confirmed_usage(
    evaluation_inputs,
    after_final,
):
    events = [ProviderDelta(text="이미 받은 부분", received_output_tokens=2)]
    if after_final:
        events.append(final_delta())
    events.append(ProviderUnavailable("외부에 노출하면 안 되는 공급자 원문"))
    report, result, _ = await run_script(evaluation_inputs, events)
    assert result["answer"] == "이미 받은 부분"
    assert result["execution"]["error"] == "provider_unavailable"
    assert result["execution"]["received_output_tokens"] == 2
    assert result["execution"]["input_tokens"] == (100 if after_final else None)
    assert result["execution"]["output_tokens"] == (3 if after_final else None)
    assert result["execution"]["usage_status"] == ("confirmed" if after_final else "received_only")
    assert "외부에 노출하면" not in json.dumps(report, ensure_ascii=False)


async def test_context_limit_stops_before_stream(evaluation_inputs):
    _, settings, options, _, _ = evaluation_inputs
    report, result, provider = await run_script(
        evaluation_inputs,
        [ProviderDelta(text="생성되면 안 됨")],
        counted=settings.llm_context_window - options.max_tokens + 1,
    )
    assert report["quality_status"] == "execution_failed"
    assert result["execution"]["error"] == "context_limit"
    assert provider.stream_calls == 0
    assert result["execution"]["input_tokens"] is None


@pytest.mark.parametrize("counted", [None, True, 0, -1])
async def test_invalid_input_measurement_stops_before_stream(evaluation_inputs, counted):
    _, result, provider = await run_script(evaluation_inputs, [], counted=counted)
    assert result["execution"]["error"] == "invalid_input_count"
    assert provider.stream_calls == 0


async def test_cancelled_run_checkpoints_partial_case_and_leaves_remaining_not_run(
    evaluation_inputs,
):
    dataset, settings, options, _, _ = evaluation_inputs
    report, prompts = runner.prepare_report(
        dataset,
        DEFAULT_DATASET,
        settings,
        options,
        model=True,
        selected=[case.id for case in dataset.cases[:2]],
    )
    checkpoints = []
    provider = ScriptedProvider(
        [
            ProviderDelta(text="중단 직전 답변", received_output_tokens=2),
            asyncio.CancelledError(),
        ]
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.run_model(
            report,
            prompts,
            dataset,
            settings,
            options,
            provider=provider,
            checkpoint=lambda value: checkpoints.append(copy.deepcopy(value)),
        )
    assert checkpoints
    saved = checkpoints[-1]
    assert saved["quality_status"] == "execution_failed"
    assert saved["completed_cases"] == 0
    assert saved["cases"][0]["execution"]["error"] == "interrupted"
    assert saved["cases"][0]["answer"] == "중단 직전 답변"
    assert saved["cases"][0]["execution"]["received_output_tokens"] == 2
    assert saved["cases"][1]["execution"]["status"] == "not_run"
    assert saved["cases"][1]["answer"] is None
    assert provider.stream_calls == 1
    assert provider.closed


def test_report_output_preserves_existing_file_and_uses_private_permissions(tmp_path):
    path = tmp_path / "reports" / "baseline.json"
    cli.write_json(path, {"original": "이전 평가"})
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="이미"):
        cli.write_json(path, {"replacement": "새 평가"})
    assert path.read_bytes() == original
    assert list(path.parent.glob(".evaluation-*")) == []
    cli.write_json(path, {"checkpoint": "동일 실행의 진행 결과"}, replace=True)
    assert json.loads(path.read_text())["checkpoint"] == "동일 실행의 진행 결과"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_run_cli_rejects_existing_output_before_model_execution(tmp_path, monkeypatch, capsys):
    path = tmp_path / "existing.json"
    path.write_text("기존 평가 보고서", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("이미 존재하는 보고서 경로로 모델을 실행했습니다.")

    monkeypatch.setattr(cli, "run_model", forbidden)
    assert cli.main(["run", "--output", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == "기존 평가 보고서"
    assert "이미 있습니다" in capsys.readouterr().err
