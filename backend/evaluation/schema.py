"""고정 평가 자료의 형식과 평가 전용 모델 접속 범위를 검증한다."""

import ipaddress
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_DATASET = Path(__file__).parent / "fixtures" / "answer-quality-v1.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Criterion(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    dimension: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool
    score_anchors: dict[str, str]

    @model_validator(mode="after")
    def anchors(self) -> Self:
        if set(self.score_anchors) != {"0", "1", "2"} or any(
            not value.strip() for value in self.score_anchors.values()
        ):
            raise ValueError("각 기준에는 0·1·2점의 설명이 필요합니다.")
        return self


class LexicalCheck(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    kind: Literal["contains_any", "excludes_all"]
    terms: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def nonempty_terms(self) -> Self:
        if any(not term.strip() for term in self.terms):
            raise ValueError("자동 진단의 검색어는 비어 있을 수 없습니다.")
        return self


class Turn(StrictModel):
    user: str = Field(min_length=1, max_length=100_000)
    assistant: str | None
    status: Literal["completed", "cancelled", "failed"]
    finish_reason: Literal["stop", "length"] | None


class QualityCase(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    category: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=100_000)
    expected_language: str = Field(min_length=1)
    turns: list[Turn] = Field(max_length=200)
    summary: str | None
    reference_kind: Literal["web", "file"] | None
    reference_context: str | None
    criteria: list[Criterion] = Field(min_length=1, max_length=20)
    lexical_checks: list[LexicalCheck]

    @model_validator(mode="after")
    def consistent_case(self) -> Self:
        if not self.question.strip():
            raise ValueError("평가 질문은 비어 있을 수 없습니다.")
        if (self.reference_kind is None) != (self.reference_context is None):
            raise ValueError("참고 자료 종류와 내용은 함께 지정해야 합니다.")
        if self.reference_context is not None and not self.reference_context.strip():
            raise ValueError("참고 자료는 비어 있을 수 없습니다.")
        for items in (self.criteria, self.lexical_checks):
            if len({item.id for item in items}) != len(items):
                raise ValueError("사례 안의 평가 기준·진단 ID는 각각 고유해야 합니다.")
        return self


class Rubric(StrictModel):
    version: Literal[1]
    score_levels: dict[str, str]
    dimensions: dict[str, str]

    @model_validator(mode="after")
    def complete_levels(self) -> Self:
        if set(self.score_levels) != {"0", "1", "2"} or not self.dimensions:
            raise ValueError("평가 축과 0·1·2점의 공통 설명이 필요합니다.")
        return self


class Dataset(StrictModel):
    schema_version: Literal[1]
    fixture_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    rubric: Rubric
    cases: list[QualityCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("평가 사례 ID는 중복될 수 없습니다.")
        if any(
            criterion.dimension not in self.rubric.dimensions
            for case in self.cases
            for criterion in case.criteria
        ):
            raise ValueError("공통 평가표에 없는 평가 축입니다.")
        return self


def load_dataset(path: Path = DEFAULT_DATASET) -> Dataset:
    try:
        return Dataset.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise ValueError("평가 자료를 읽거나 형식을 검증하지 못했습니다.") from error


def local_model_url(value: str) -> str:
    """평가 입력은 인증 정보 없는 loopback HTTP 모델 주소에만 보낸다."""
    try:
        parsed = urlsplit(value)
        loopback = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname or "").is_loopback
        )
        valid = (
            parsed.scheme == "http"
            and loopback
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("인증 정보 없는 loopback HTTP 모델 주소가 필요합니다.")
    return value
