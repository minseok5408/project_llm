"""답변에 결합된 사람 채점과 동일 평가셋의 기준선 회귀를 검증한다."""

import hashlib
import json
import re
from datetime import datetime

POLICY = {
    "version": 1,
    "minimum_case_score": 0.75,
    "minimum_overall_score": 0.90,
    "maximum_overall_regression": 0.02,
    "maximum_case_regression": 0.25,
}


def canonical_hash(value: object) -> str:
    """비밀값을 오류에 포함하지 않고 정규 JSON의 SHA256을 계산한다."""
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, OverflowError):
        raise ValueError("평가 자료를 유효한 JSON으로 직렬화할 수 없습니다.") from None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _timestamp(value: object) -> bool:
    if not _text(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _cases(report: dict) -> dict[tuple[str, int], dict]:
    """채점에 필요한 보고서 구조와 중복·답변 변조를 검사한다."""
    _require(isinstance(report, dict), "평가 보고서 형식이 올바르지 않습니다.")
    _require(
        type(report.get("schema_version")) is int and report["schema_version"] == 1,
        "지원하지 않는 평가 보고서 버전입니다.",
    )
    _require(report.get("mode") in ("contract", "model"), "평가 실행 모드가 올바르지 않습니다.")
    _require(
        report.get("scope") == "synthetic_answer_only",
        "지원하지 않는 평가 범위입니다.",
    )
    _require(
        _text(report.get("run_id")) and _timestamp(report.get("created_at")),
        "평가 실행 식별자 또는 생성 시각이 올바르지 않습니다.",
    )
    dataset = report.get("dataset")
    _require(
        isinstance(dataset, dict)
        and _text(dataset.get("id"))
        and _hash(dataset.get("sha256"))
        and isinstance(dataset.get("rubric"), dict)
        and type(dataset["rubric"].get("version")) is int
        and dataset["rubric"]["version"] == 1,
        "평가 자료 또는 채점 기준 식별 정보가 올바르지 않습니다.",
    )
    configuration = report.get("configuration")
    _require(
        isinstance(configuration, dict)
        and _integer(configuration.get("max_tokens"), 1)
        and configuration["max_tokens"] <= 4096
        and _integer(configuration.get("context_window"), 1)
        and _integer(configuration.get("repeat"), 1)
        and configuration["repeat"] <= 5,
        "평가 생성 설정이 올바르지 않습니다.",
    )
    entries = report.get("cases")
    _require(isinstance(entries, list) and bool(entries), "평가 사례가 하나 이상 필요합니다.")
    indexed = {}
    for case in entries:
        _require(isinstance(case, dict), "평가 사례 형식이 올바르지 않습니다.")
        _require(
            _text(case.get("case_id"))
            and _integer(case.get("repeat"), 1)
            and case["repeat"] <= configuration["repeat"],
            "평가 사례 식별자 또는 반복 번호가 올바르지 않습니다.",
        )
        key = case["case_id"], case["repeat"]
        _require(key not in indexed, "중복된 평가 사례가 있습니다.")
        _require(
            _hash(case.get("input_sha256")) and _hash(case.get("case_definition_sha256")),
            "평가 사례 입력 또는 정의 해시가 올바르지 않습니다.",
        )
        if "input_messages" in case:
            messages = case["input_messages"]
            _require(
                isinstance(messages, list)
                and bool(messages)
                and all(
                    isinstance(message, dict)
                    and set(message) == {"role", "content"}
                    and message["role"] in ("system", "user", "assistant")
                    and _text(message["content"])
                    for message in messages
                ),
                "보존된 모델 입력 메시지 형식이 올바르지 않습니다.",
            )
            _require(
                case["input_sha256"] == canonical_hash(messages),
                "보존된 모델 입력과 입력 해시가 일치하지 않습니다.",
            )
        _require(
            "answer" in case and "answer_sha256" in case,
            "평가 답변 또는 답변 해시 필드가 누락되었습니다.",
        )
        answer = case["answer"]
        _require(answer is None or isinstance(answer, str), "평가 답변 형식이 올바르지 않습니다.")
        _require(
            case.get("answer_sha256") == (None if answer is None else canonical_hash(answer)),
            "평가 답변과 답변 해시가 일치하지 않습니다.",
        )
        execution = case.get("execution")
        _require(
            isinstance(execution, dict)
            and execution.get("status") in ("not_run", "completed", "failed"),
            "평가 실행 상태가 올바르지 않습니다.",
        )
        criteria = case.get("criteria")
        _require(isinstance(criteria, list) and bool(criteria), "사례별 채점 항목이 필요합니다.")
        identifiers = set()
        for criterion in criteria:
            _require(
                isinstance(criterion, dict)
                and _text(criterion.get("id"))
                and _text(criterion.get("dimension"))
                and _text(criterion.get("description"))
                and type(criterion.get("required")) is bool,
                "채점 항목 정의가 올바르지 않습니다.",
            )
            _require(criterion["id"] not in identifiers, "중복된 채점 항목이 있습니다.")
            anchors = criterion.get("score_anchors")
            _require(
                isinstance(anchors, dict)
                and set(anchors) == {"0", "1", "2"}
                and all(_text(value) for value in anchors.values()),
                "채점 항목의 점수별 기준이 올바르지 않습니다.",
            )
            identifiers.add(criterion["id"])
        indexed[key] = case
    expected_repeat = set(range(1, configuration["repeat"] + 1))
    for identifier in {key[0] for key in indexed}:
        _require(
            {key[1] for key in indexed if key[0] == identifier} == expected_repeat,
            "평가 사례의 반복 실행이 누락되었습니다.",
        )
    if "case_ids" in dataset:
        identifiers = dataset["case_ids"]
        _require(
            isinstance(identifiers, list)
            and bool(identifiers)
            and all(_text(identifier) for identifier in identifiers)
            and len(set(identifiers)) == len(identifiers)
            and {key[0] for key in indexed} <= set(identifiers),
            "평가 자료의 전체 사례 목록이 올바르지 않습니다.",
        )
    if "total_cases" in report:
        _require(
            type(report["total_cases"]) is int and report["total_cases"] == len(indexed),
            "평가 사례 전체 개수가 일치하지 않습니다.",
        )
    return indexed


def review_template(report: dict) -> dict:
    """원 보고서와 각 답변 해시에 결합된 미채점 양식을 만든다."""
    cases = _cases(report)
    return {
        "schema_version": 1,
        "report_sha256": canonical_hash(report),
        "reviewer": "",
        "reviewed_at": "",
        "cases": [
            {
                "case_id": case["case_id"],
                "repeat": case["repeat"],
                "answer_sha256": case["answer_sha256"],
                "criteria": [
                    {"id": criterion["id"], "score": None, "notes": ""}
                    for criterion in case["criteria"]
                ],
            }
            for case in cases.values()
        ],
    }


def _reviews(report: dict, review: dict, cases: dict) -> tuple[dict, bool]:
    _require(isinstance(review, dict), "사람 채점 자료 형식이 올바르지 않습니다.")
    _require(
        type(review.get("schema_version")) is int and review["schema_version"] == 1,
        "지원하지 않는 사람 채점 자료 버전입니다.",
    )
    _require(
        review.get("report_sha256") == canonical_hash(report),
        "채점 자료가 현재 평가 보고서와 일치하지 않습니다.",
    )
    _require(
        isinstance(review.get("reviewer"), str) and isinstance(review.get("reviewed_at"), str),
        "채점자 또는 채점 시각 형식이 올바르지 않습니다.",
    )
    if review["reviewed_at"].strip():
        _require(
            _timestamp(review["reviewed_at"]), "채점 시각은 시간대가 있는 ISO 시각이어야 합니다."
        )
    complete = bool(review["reviewer"].strip() and review["reviewed_at"].strip())
    entries = review.get("cases")
    _require(isinstance(entries, list), "사례별 사람 채점 자료가 필요합니다.")
    indexed = {}
    for entry in entries:
        _require(
            isinstance(entry, dict)
            and _text(entry.get("case_id"))
            and _integer(entry.get("repeat"), 1),
            "사람 채점 사례 식별자가 올바르지 않습니다.",
        )
        key = entry["case_id"], entry["repeat"]
        _require(key in cases and key not in indexed, "알 수 없거나 중복된 사람 채점 사례입니다.")
        _require(
            entry.get("answer_sha256") == cases[key]["answer_sha256"],
            "사람 채점 대상 답변이 현재 답변과 일치하지 않습니다.",
        )
        criteria = entry.get("criteria")
        _require(isinstance(criteria, list), "사례별 사람 채점 항목이 필요합니다.")
        expected = {item["id"] for item in cases[key]["criteria"]}
        scores = {}
        for criterion in criteria:
            _require(
                isinstance(criterion, dict) and _text(criterion.get("id")),
                "사람 채점 항목 식별자가 올바르지 않습니다.",
            )
            identifier = criterion["id"]
            _require(
                identifier in expected and identifier not in scores,
                "알 수 없거나 중복된 사람 채점 항목입니다.",
            )
            _require("score" in criterion, "사람 채점 점수 필드가 누락되었습니다.")
            score = criterion["score"]
            _require(
                score is None or (type(score) is int and 0 <= score <= 2),
                "사람 채점 점수는 0부터 2 사이 정수 또는 미채점 값이어야 합니다.",
            )
            _require(isinstance(criterion.get("notes"), str), "채점 근거는 문자열이어야 합니다.")
            scores[identifier] = criterion
        _require(set(scores) == expected, "필수 사람 채점 항목이 누락되었습니다.")
        indexed[key] = scores
    _require(set(indexed) == set(cases), "사람 채점 사례가 누락되었습니다.")
    return indexed, complete


def _execution_failures(case: dict, configuration: dict) -> list[str]:
    """완료 표시만 믿지 않고 답변·종료 이유·확정 사용량을 다시 확인한다."""
    execution = case["execution"]
    max_tokens = configuration["max_tokens"]
    failures = []
    if execution["status"] != "completed":
        failures.append("모델 실행이 완료되지 않았습니다.")
    if not _text(case["answer"]):
        failures.append("완성된 모델 답변이 없습니다.")
    if execution.get("finish_reason") != "stop":
        failures.append("모델 답변이 정상 종료되지 않았습니다.")
    if execution.get("error") is not None:
        failures.append("모델 실행 오류가 기록되어 있습니다.")
    counted, actual = execution.get("counted_input_tokens"), execution.get("input_tokens")
    output, received = execution.get("output_tokens"), execution.get("received_output_tokens")
    if not (_integer(counted, 1) and _integer(actual, 1) and counted == actual):
        failures.append("입력 토큰 계산과 확정 사용량이 일치하지 않습니다.")
    elif counted + max_tokens > configuration["context_window"]:
        failures.append("입력과 출력 상한의 합이 모델 문맥 한도를 초과합니다.")
    if not (_integer(output, 1) and output <= max_tokens):
        failures.append("확정 출력 토큰 사용량이 올바르지 않습니다.")
    if received is not None and not (
        _integer(received) and _integer(output) and received <= output
    ):
        failures.append("수신 중 확인한 출력 토큰 사용량이 올바르지 않습니다.")
    return failures


def assess(report: dict, review: dict) -> dict:
    """자동 진단으로 사람의 의미 채점을 대신하지 않고 품질 기준을 적용한다."""
    cases = _cases(report)
    reviews, reviewer_complete = _reviews(report, review, cases)
    result = {
        "policy": dict(POLICY),
        "scope": (
            "unspecified"
            if "case_ids" not in report["dataset"]
            else "full"
            if {key[0] for key in cases} == set(report["dataset"]["case_ids"])
            else "subset"
        ),
        "status": "not_run",
        "passed": None,
        "overall_score": None,
        "cases": [],
        "failures": [],
    }
    total_points, total_criteria = 0, 0
    pending, execution_failed = False, False
    for key, case in cases.items():
        criteria = []
        scores = reviews[key]
        incomplete = not reviewer_complete
        for definition in case["criteria"]:
            scored = scores[definition["id"]]
            incomplete |= scored["score"] is None or not scored["notes"].strip()
            criteria.append({**scored, "required": definition["required"]})
        item = {
            "case_id": key[0],
            "repeat": key[1],
            "status": "not_run",
            "score": None,
            "criteria": criteria,
            "failures": [],
        }
        result["cases"].append(item)
        if report["mode"] == "contract":
            continue
        failures = _execution_failures(case, report["configuration"])
        if failures:
            item.update(status="failed", failures=failures)
            execution_failed = True
        elif incomplete:
            item["status"] = "pending_review"
            pending = True
        else:
            points = sum(criterion["score"] for criterion in criteria)
            item["score"] = points / (2 * len(criteria))
            total_points += points
            total_criteria += len(criteria)
            if any(criterion["required"] and criterion["score"] == 0 for criterion in criteria):
                failures.append("필수 채점 항목에서 0점을 받았습니다.")
            if item["score"] < POLICY["minimum_case_score"]:
                failures.append("사례별 최소 품질 점수를 충족하지 못했습니다.")
            item.update(status="failed" if failures else "passed", failures=failures)
        if failures:
            result["failures"].append(
                {"case_id": key[0], "repeat": key[1], "reasons": list(failures)}
            )
    if report["mode"] == "contract":
        return result
    if execution_failed:
        result.update(status="failed", passed=False)
    elif pending:
        result["status"] = "pending_review"
    else:
        result["overall_score"] = total_points / (2 * total_criteria)
        if result["overall_score"] < POLICY["minimum_overall_score"]:
            result["failures"].append({"reasons": ["전체 최소 품질 점수를 충족하지 못했습니다."]})
        passed = not result["failures"]
        result.update(status="passed" if passed else "failed", passed=passed)
    return result


def _scored(value: dict) -> tuple[dict, dict, dict]:
    _require(
        isinstance(value, dict)
        and type(value.get("schema_version")) is int
        and value["schema_version"] == 1,
        "채점 완료 보고서 형식이 올바르지 않습니다.",
    )
    report, review = value.get("report"), value.get("review")
    assessment = assess(report, review)
    cases = _cases(report)
    _require(
        report["mode"] == "model"
        and assessment["status"] in ("passed", "failed")
        and assessment["overall_score"] is not None
        and all(not _execution_failures(case, report["configuration"]) for case in cases.values()),
        "모든 모델 실행과 사람 채점을 완료해야 기준선을 비교할 수 있습니다.",
    )
    _require(
        assessment["scope"] == "full",
        "평가 자료의 전체 사례를 실행하고 채점해야 기준선으로 비교할 수 있습니다.",
    )
    return report, assessment, cases


def compare(candidate_scored: dict, baseline_scored: dict) -> dict:
    """저장된 성공 표시를 신뢰하지 않고 재채점한 동일 사례의 회귀를 비교한다."""
    candidate, candidate_assessment, candidate_cases = _scored(candidate_scored)
    baseline, baseline_assessment, baseline_cases = _scored(baseline_scored)
    _require(
        baseline_assessment["passed"] is True,
        "품질 기준을 통과한 보고서만 기준선으로 쓸 수 있습니다.",
    )
    _require(
        candidate["dataset"] == baseline["dataset"], "평가 자료 또는 채점 기준이 서로 다릅니다."
    )
    _require(
        set(candidate_cases) == set(baseline_cases), "비교할 사례 또는 반복 실행 집합이 다릅니다."
    )
    for key, case in candidate_cases.items():
        previous = baseline_cases[key]
        _require(
            case["case_definition_sha256"] == previous["case_definition_sha256"]
            and {item["id"]: item for item in case["criteria"]}
            == {item["id"]: item for item in previous["criteria"]},
            "비교할 사례 정의 또는 채점 항목이 다릅니다.",
        )
    baseline_scores = {
        (item["case_id"], item["repeat"]): item["score"] for item in baseline_assessment["cases"]
    }
    differences = {}
    for field in ("configuration", "implementation"):
        if candidate.get(field) != baseline.get(field):
            differences[field] = {
                "baseline": baseline.get(field),
                "candidate": candidate.get(field),
            }
    delta = candidate_assessment["overall_score"] - baseline_assessment["overall_score"]
    result = {
        "policy": dict(POLICY),
        "status": "passed",
        "passed": True,
        "overall_delta": delta,
        "case_deltas": [],
        "differences": differences,
        "failures": [],
    }
    if not candidate_assessment["passed"]:
        result["failures"].append("후보가 절대 품질 기준을 충족하지 못했습니다.")
    if delta < -POLICY["maximum_overall_regression"] - 1e-12:
        result["failures"].append("전체 점수 하락이 허용 회귀 폭을 초과했습니다.")
    for item in candidate_assessment["cases"]:
        key = item["case_id"], item["repeat"]
        previous = baseline_scores[key]
        change = item["score"] - previous
        result["case_deltas"].append(
            {
                "case_id": key[0],
                "repeat": key[1],
                "baseline_score": previous,
                "candidate_score": item["score"],
                "delta": change,
            }
        )
        if change < -POLICY["maximum_case_regression"] - 1e-12:
            result["failures"].append("사례별 점수 하락이 허용 회귀 폭을 초과했습니다.")
    if result["failures"]:
        result.update(status="failed", passed=False)
    return result
