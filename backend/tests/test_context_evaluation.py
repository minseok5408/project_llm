"""오프라인 검사와 실제 모델 어휘 지표를 혼동하지 않고 실패를 드러내는지 확인한다."""

import argparse
import json
import subprocess
import sys

import pytest

from backend.app.config import Settings
from backend.app.context.compaction import SUMMARY_PROMPT
from backend.app.llm.protocol import ProviderDelta
from backend.app.llm.providers.mock import MockProvider
from backend.context_evaluation import evaluate, lexical_score
from backend.evaluation_cases import cases
from scripts.evaluate_context import local_model_url


def configured() -> Settings:
    return Settings(
        _env_file=None,
        llm_backend="mock",
        llm_context_window=1024,
        llm_compaction_max_tokens=128,
        llm_compaction_trigger_ratio=0.75,
        llm_compaction_target_ratio=0.55,
        llm_compaction_keep_turns=4,
    )


def test_lexical_score_detects_occupation_missing_from_name_only_answer() -> None:
    expected = {"이름": ("이가온",), "직업": ("웹 개발자",)}
    incomplete = lexical_score("이가온입니다.", expected)
    assert incomplete["recall"] == 0.5
    assert incomplete["missing"] == ["직업"]
    complete = lexical_score("이가온 님은 웹개발자입니다.", expected)
    assert complete["recall"] == 1.0
    assert complete["missing"] == []


async def test_offline_report_keeps_semantic_metrics_unmeasured_and_repeats_compaction() -> None:
    report = await evaluate(cases(), configured())
    assert report["passed"]
    assert report["model"] is None
    assert report["mode"] == "contract"
    assert report["lexical_recall"] is None
    assert report["context_policy_version"] == 2
    assert len(report["context_prompt_sha256"]) == 64
    assert len(report["summary_prompt_sha256"]) == 64
    assert all(item["model_evaluation"] == "not_run" for item in report["cases"])
    assert all(item["answer_usage"] is None for item in report["cases"])
    assert all(item["summary_usage"] == [] for item in report["cases"])
    rolling = next(item for item in report["cases"] if item["case"] == "rolling_108_turns")
    assert rolling["turn_count"] == 108
    assert rolling["compaction_count"] >= 2
    assert rolling["through_sequence"] > 0
    assert rolling["remaining_turns"] >= 1


class EvaluationProvider(MockProvider):
    """평가 도구의 집계와 실패 분기를 시험한다. 모델 추론 품질을 모의하지 않는다."""

    def __init__(self, *, missing_usage=False, summary_reason="stop"):
        super().__init__(configured(), delay_seconds=0)
        self.missing_usage = missing_usage
        self.summary_reason = summary_reason

    async def stream(self, messages, options):
        is_summary = messages[0].content == SUMMARY_PROMPT
        yield ProviderDelta(text="계약 테스트용 요약." if is_summary else "이가온입니다.")
        if not self.missing_usage:
            yield ProviderDelta(
                input_tokens=await self.count_input(messages, options),
                output_tokens=3,
                final=True,
                finish_reason=self.summary_reason if is_summary else "stop",
            )


async def test_model_report_records_usage_and_fails_missing_fact_threshold() -> None:
    report = await evaluate(cases()[:1], configured(), provider=EvaluationProvider())
    assert report["passed"] is False
    assert report["mode"] == "model"
    assert report["model"] == configured().llm_model_id
    assert report["lexical_recall"] == 0.5
    assert report["cases"][0]["model_evaluation"] == "completed"
    assert report["cases"][0]["answer_usage"]["output_tokens"] == 3
    assert report["cases"][0]["lexical"]["missing"] == ["직업"]


async def test_model_report_fails_missing_final_usage() -> None:
    report = await evaluate(
        cases()[:1], configured(), provider=EvaluationProvider(missing_usage=True)
    )
    assert report["passed"] is False
    assert report["cases"][0]["model_evaluation"] == "failed"
    assert report["cases"][0]["answer_usage"] is None
    assert report["lexical_recall"] is None


async def test_model_report_retains_summary_usage_but_rejects_truncated_summary() -> None:
    report = await evaluate(
        cases()[-1:], configured(), provider=EvaluationProvider(summary_reason="length")
    )
    result = report["cases"][0]
    assert report["passed"] is False
    assert result["model_evaluation"] == "failed"
    assert result["summary_usage"][0]["finish_reason"] == "length"
    assert result["summary_usage"][0]["output_tokens"] == 3
    assert result["answer_usage"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://192.168.0.2:8080/v1",
        "http://user:secret@127.0.0.1:8080/v1",
        "http://localhost:8080/v1?key=secret",
        "http://127.0.0.1:70000/v1",
        "http://127.0.0.1:8080/v1#fragment",
        "http://[not-an-address/v1",
    ],
)
def test_model_cli_rejects_remote_or_credential_bearing_address(url: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        local_model_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://[::1]:8080/v1"])
def test_model_cli_accepts_loopback_address(url: str) -> None:
    assert local_model_url(url) == url


def test_cli_defaults_to_contract_mode_even_if_model_address_is_unavailable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/evaluate_context.py", "--base-url", "http://127.0.0.1:1/v1"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "contract"
    assert report["passed"] is True
    assert report["model"] is None
