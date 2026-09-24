"""미완료 실행·단어 일치·변조된 채점이 품질 통과로 승격되지 않는지 검사한다."""

from copy import deepcopy

import pytest

from backend.evaluation.scoring import assess, canonical_hash, compare, review_template


def report(*, count=1, mode="model"):
    """모델 추론을 흉내 내지 않는 고정 채점 계약 자료를 만든다."""
    cases = []
    for index in range(count):
        answer = "정해진 필수 단어를 모두 포함하지만 실제 의미 검토가 필요한 답변입니다."
        cases.append(
            {
                "case_id": f"case_{index}",
                "repeat": 1,
                "category": "facts",
                "question": "자료에 있는 사실을 답해주세요.",
                "expected_language": "ko",
                "case_definition_sha256": canonical_hash({"case": index}),
                "input_sha256": canonical_hash(["system", "question"]),
                "answer": answer if mode == "model" else None,
                "answer_sha256": canonical_hash(answer) if mode == "model" else None,
                "criteria": [
                    {
                        "id": f"criterion_{criterion}",
                        "dimension": "correctness",
                        "description": "근거의 사실을 올바르게 답했는지 검토한다.",
                        "required": True,
                        "score_anchors": {"0": "오류", "1": "부분 충족", "2": "완전 충족"},
                    }
                    for criterion in range(4)
                ],
                "execution": {
                    "status": "completed" if mode == "model" else "not_run",
                    "error": None,
                    "counted_input_tokens": 10,
                    "input_tokens": 10,
                    "output_tokens": 12,
                    "received_output_tokens": 12,
                    "finish_reason": "stop",
                    "elapsed_seconds": 1.0,
                    "first_token_seconds": 0.1,
                },
                "diagnostics": {"lexical": [{"matched": True}]},
            }
        )
    return {
        "schema_version": 1,
        "mode": mode,
        "scope": "synthetic_answer_only",
        "run_id": "test-run",
        "created_at": "2026-09-22T00:00:00+00:00",
        "dataset": {
            "id": "test-fixture",
            "sha256": canonical_hash("fixture"),
            "rubric": {"version": 1},
            "case_ids": [case["case_id"] for case in cases],
        },
        "implementation": {
            "commit": "test",
            "dirty": False,
            "code_sha256": canonical_hash("code"),
            "prompt_policy_sha256": canonical_hash("prompt"),
            "packages": {},
        },
        "configuration": {
            "model": "local-test",
            "context_window": 4096,
            "max_tokens": 128,
            "thinking": False,
            "timeout_seconds": 120,
            "repeat": 1,
            "sampling": {"temperature": 0.7},
        },
        "cases": cases,
        "total_cases": count,
    }


def reviewed(value):
    review = review_template(value)
    review["reviewer"] = "검토자"
    review["reviewed_at"] = "2026-09-22T00:05:00+00:00"
    for case in review["cases"]:
        for criterion in case["criteria"]:
            criterion.update(score=2, notes="자료의 사실과 답변을 대조하여 확인했습니다.")
    return review


def scored(value, review=None):
    review = reviewed(value) if review is None else review
    return {
        "schema_version": 1,
        "report": value,
        "review": review,
        "assessment": assess(value, review),
    }


def test_canonical_hash_is_stable_and_rejects_non_json_numbers():
    assert canonical_hash({"b": 2, "a": "한글"}) == canonical_hash({"a": "한글", "b": 2})
    with pytest.raises(ValueError, match="JSON"):
        canonical_hash(float("nan"))


def test_contract_mode_never_claims_model_quality_even_with_filled_review():
    value = report(mode="contract")
    result = assess(value, reviewed(value))
    assert result["status"] == "not_run"
    assert result["passed"] is None
    assert result["overall_score"] is None


def test_lexical_matches_without_human_review_do_not_pass():
    value = report()
    result = assess(value, review_template(value))
    assert result["status"] == "pending_review"
    assert result["passed"] is None
    assert result["overall_score"] is None


@pytest.mark.parametrize("field", ["reviewer", "reviewed_at"])
def test_reviewer_identity_and_timestamp_are_required(field):
    value = report()
    review = reviewed(value)
    review[field] = ""
    assert assess(value, review)["status"] == "pending_review"


def test_every_criterion_requires_a_score_and_reason():
    value = report()
    review = reviewed(value)
    review["cases"][0]["criteria"][0]["score"] = None
    assert assess(value, review)["status"] == "pending_review"
    review["cases"][0]["criteria"][0].update(score=2, notes=" ")
    assert assess(value, review)["status"] == "pending_review"


@pytest.mark.parametrize("score", [True, False, -1, 3, 1.0, "2", float("nan")])
def test_scores_are_strict_integers(score):
    value = report()
    review = reviewed(value)
    review["cases"][0]["criteria"][0]["score"] = score
    with pytest.raises(ValueError, match="점수"):
        assess(value, review)


def test_required_zero_fails_even_when_overall_and_case_thresholds_pass():
    value = report(count=10)
    review = reviewed(value)
    review["cases"][0]["criteria"][0]["score"] = 0
    result = assess(value, review)
    assert result["overall_score"] == 0.975
    assert result["cases"][0]["score"] == 0.75
    assert result["status"] == "failed"
    assert any("필수" in reason for reason in result["cases"][0]["failures"])


@pytest.mark.parametrize(
    "change",
    [
        {"status": "not_run"},
        {"status": "failed"},
        {"finish_reason": "length"},
        {"finish_reason": None},
        {"input_tokens": None},
        {"input_tokens": 11},
        {"counted_input_tokens": True},
        {"output_tokens": True},
        {"output_tokens": -1},
        {"output_tokens": 0},
        {"output_tokens": 129},
        {"received_output_tokens": 13},
        {"error": "실행 실패"},
    ],
)
def test_execution_labels_cannot_hide_invalid_usage_or_incomplete_answers(change):
    value = report()
    value["cases"][0]["execution"].update(change)
    result = assess(value, reviewed(value))
    assert result["status"] == "failed"
    assert result["passed"] is False
    assert result["overall_score"] is None


def test_empty_answer_is_execution_failure():
    value = report()
    value["cases"][0].update(answer=" ", answer_sha256=canonical_hash(" "))
    assert assess(value, reviewed(value))["status"] == "failed"


@pytest.mark.parametrize("field", ["answer", "answer_sha256"])
def test_missing_answer_fields_are_validation_errors(field):
    value = report()
    del value["cases"][0][field]
    with pytest.raises(ValueError, match="누락"):
        review_template(value)


def test_input_and_output_budget_must_fit_context_window():
    value = report()
    value["configuration"]["context_window"] = 130
    assert assess(value, reviewed(value))["status"] == "failed"


def test_execution_failure_takes_precedence_over_unfinished_human_review():
    value = report(count=2)
    value["cases"][1]["execution"]["status"] = "not_run"
    result = assess(value, review_template(value))
    assert result["status"] == "failed"
    assert result["overall_score"] is None


@pytest.mark.parametrize("target", ["answer", "input", "configuration", "dataset"])
def test_old_review_cannot_be_reused_after_report_changes(target):
    value = report()
    review = reviewed(value)
    if target == "answer":
        value["cases"][0].update(answer="바뀐 답변", answer_sha256=canonical_hash("바뀐 답변"))
    elif target == "input":
        value["cases"][0]["input_sha256"] = canonical_hash("다른 입력")
    elif target == "configuration":
        value["configuration"]["thinking"] = True
    else:
        value["dataset"]["sha256"] = canonical_hash("다른 자료")
    with pytest.raises(ValueError, match="보고서"):
        assess(value, review)


def test_answer_tampering_is_detected_even_with_new_review_digest():
    value = report()
    value["cases"][0]["answer"] = "해시를 갱신하지 않은 답변"
    with pytest.raises(ValueError, match="답변 해시"):
        review_template(value)


def test_preserved_input_messages_are_bound_to_their_digest():
    value = report()
    case = value["cases"][0]
    case["input_messages"] = [{"role": "user", "content": "실제 평가 질문"}]
    case["input_sha256"] = canonical_hash(case["input_messages"])
    assert assess(value, reviewed(value))["passed"] is True
    case["input_messages"][0]["content"] = "다른 질문"
    with pytest.raises(ValueError, match="입력 해시"):
        review_template(value)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        "문자열",
        [{"role": "user"}],
        [{"role": "tool", "content": "내용"}],
        [{"role": "user", "content": " "}],
        [{"role": "user", "content": "내용", "extra": 1}],
    ],
)
def test_malformed_preserved_input_is_rejected(messages):
    value = report()
    value["cases"][0]["input_messages"] = messages
    with pytest.raises(ValueError, match="입력 메시지"):
        review_template(value)


@pytest.mark.parametrize(
    "change",
    [
        "missing_case",
        "unknown_case",
        "duplicate_case",
        "missing_criterion",
        "unknown_criterion",
        "duplicate_criterion",
        "answer_hash",
    ],
)
def test_missing_duplicate_and_unknown_review_entries_are_rejected(change):
    value = report()
    review = reviewed(value)
    if change == "missing_case":
        review["cases"] = []
    elif change == "unknown_case":
        review["cases"][0]["case_id"] = "unknown"
    elif change == "duplicate_case":
        review["cases"].append(deepcopy(review["cases"][0]))
    elif change == "missing_criterion":
        review["cases"][0]["criteria"].pop()
    elif change == "unknown_criterion":
        review["cases"][0]["criteria"][0]["id"] = "unknown"
    elif change == "duplicate_criterion":
        review["cases"][0]["criteria"].append(deepcopy(review["cases"][0]["criteria"][0]))
    else:
        review["cases"][0]["answer_sha256"] = canonical_hash("다른 답변")
    with pytest.raises(ValueError):
        assess(value, review)


def test_empty_dataset_or_missing_repeated_run_cannot_pass():
    value = report(count=0)
    with pytest.raises(ValueError, match="사례"):
        review_template(value)
    value = report()
    value["configuration"]["repeat"] = 2
    with pytest.raises(ValueError, match="반복"):
        review_template(value)


def test_model_and_prompt_changes_are_reported_without_blocking_comparison():
    baseline = scored(report())
    value = report()
    value["configuration"]["model"] = "changed-local-model"
    value["implementation"]["prompt_policy_sha256"] = canonical_hash("다른 정책")
    result = compare(scored(value), baseline)
    assert result["passed"] is True
    assert set(result["differences"]) == {"configuration", "implementation"}


def test_partial_evaluation_can_be_scored_but_cannot_become_a_full_baseline():
    value = report()
    value["dataset"]["case_ids"] = ["case_0", "case_1"]
    completed = scored(value)
    assert completed["assessment"]["passed"] is True
    assert completed["assessment"]["scope"] == "subset"
    with pytest.raises(ValueError, match="전체 사례"):
        compare(completed, completed)
    value = report(count=2)
    value["dataset"]["case_ids"] = ["case_0", "case_1"]
    completed = scored(value)
    assert completed["assessment"]["scope"] == "full"
    assert compare(completed, completed)["passed"] is True


def test_unspecified_scope_cannot_be_used_for_baseline_comparison():
    value = report()
    del value["dataset"]["case_ids"]
    completed = scored(value)
    assert completed["assessment"]["passed"] is True
    assert completed["assessment"]["scope"] == "unspecified"
    with pytest.raises(ValueError, match="전체 사례"):
        compare(completed, completed)


def test_regression_comparison_recomputes_assessment_and_applies_numeric_limits():
    value = report(count=10)
    baseline = scored(value)
    review = reviewed(value)
    review["cases"][0]["criteria"][0]["score"] = 1
    candidate = scored(value, review)
    assert compare(candidate, baseline)["passed"] is True
    review["cases"][1]["criteria"][0]["score"] = 1
    candidate = scored(value, review)
    candidate["assessment"] = {"passed": True, "overall_score": 1.0}
    result = compare(candidate, baseline)
    assert result["passed"] is False
    assert result["overall_delta"] == pytest.approx(-0.025)


def test_regression_limits_accept_the_exact_boundary():
    value = report(count=25)
    baseline = scored(value)
    review = reviewed(value)
    for index in range(4):
        review["cases"][index]["criteria"][0]["score"] = 1
    result = compare(scored(value, review), baseline)
    assert result["overall_delta"] == pytest.approx(-0.02)
    assert result["passed"] is True
    review = reviewed(value)
    review["cases"][0]["criteria"][0]["score"] = 1
    review["cases"][0]["criteria"][1]["score"] = 1
    result = compare(scored(value, review), baseline)
    assert result["case_deltas"][0]["delta"] == -0.25
    assert result["passed"] is True


@pytest.mark.parametrize("change", ["dataset", "rubric", "subset", "criterion", "definition"])
def test_incompatible_baseline_is_rejected(change):
    baseline_value = report(count=2)
    value = deepcopy(baseline_value)
    if change == "dataset":
        value["dataset"]["sha256"] = canonical_hash("다른 자료")
    elif change == "rubric":
        value["dataset"]["rubric"]["description"] = "다른 기준"
    elif change == "subset":
        value["cases"].pop()
        value["total_cases"] = 1
    elif change == "criterion":
        value["cases"][0]["criteria"][0]["required"] = False
    else:
        value["cases"][0]["case_definition_sha256"] = canonical_hash("다른 사례")
    with pytest.raises(ValueError):
        compare(scored(value), scored(baseline_value))


@pytest.mark.parametrize(
    ("baseline_scope", "candidate_scope"),
    [
        ("synthetic_search", "live_search"),
        ("live_search", "synthetic_search"),
        (None, "live_search"),
    ],
)
def test_search_scope_mismatch_cannot_be_compared(baseline_scope, candidate_scope):
    baseline, candidate = report(), report()
    baseline["search_scope"], candidate["search_scope"] = baseline_scope, candidate_scope
    with pytest.raises(ValueError, match="검색 평가 범위"):
        compare(scored(candidate), scored(baseline))


@pytest.mark.parametrize("scope", [None, "synthetic_search", "live_search"])
def test_matching_search_scope_and_ordinary_reports_remain_comparable(scope):
    value = report()
    if scope is not None:
        value["search_scope"] = scope
    assert compare(scored(value), scored(value))["passed"] is True


@pytest.mark.parametrize("state", ["pending", "not_run", "failed_execution", "failed_quality"])
def test_unqualified_baseline_cannot_be_promoted(state):
    value = report(mode="contract" if state == "not_run" else "model")
    if state == "failed_execution":
        value["cases"][0]["execution"]["status"] = "failed"
    review = review_template(value) if state == "pending" else reviewed(value)
    if state == "failed_quality":
        review["cases"][0]["criteria"][0]["score"] = 0
    baseline = scored(value, review)
    baseline["assessment"] = {"passed": True, "overall_score": 1.0}
    with pytest.raises(ValueError):
        compare(scored(report()), baseline)
