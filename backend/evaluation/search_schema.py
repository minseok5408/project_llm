"""검색 판단과 근거 답변 평가의 고정 자료·외부 실행 범위를 검증한다."""

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from backend.app.tools.web_search.adapters.common import public_source_url
from backend.evaluation.schema import Dataset, QualityCase, Rubric, StrictModel

DEFAULT_SEARCH_DATASET = Path(__file__).parent / "fixtures" / "search-quality-v1.json"


class FixtureSource(StrictModel):
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4096)
    snippet: str = Field(max_length=10000)

    @model_validator(mode="after")
    def public_url(self) -> Self:
        if public_source_url(self.url) is None:
            raise ValueError("평가 출처는 공개 HTTP 주소여야 합니다.")
        return self


class FixtureResponse(StrictModel):
    checked_at: datetime
    sources: list[FixtureSource] = Field(max_length=5)

    @model_validator(mode="after")
    def aware_time(self) -> Self:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("평가 조회 시각에는 시간대가 필요합니다.")
        return self


class SearchCase(QualityCase):
    search_mode: Literal["auto", "on", "off"] = "auto"
    network_mode: Literal["online", "local"] = "online"
    expected_search: bool
    query_required: list[str] = Field(default_factory=list)
    query_forbidden: list[str] = Field(default_factory=list)
    fixture_responses: list[FixtureResponse] = Field(default_factory=list, max_length=3)
    live_eligible: bool = False

    @model_validator(mode="after")
    def search_contract(self) -> Self:
        if self.reference_kind is not None or self.reference_context is not None:
            raise ValueError("검색 평가 근거는 검색 응답으로만 제공해야 합니다.")
        if self.expected_search and (self.network_mode == "local" or self.search_mode == "off"):
            raise ValueError("검색을 차단한 사례는 외부 검색을 기대할 수 없습니다.")
        if any(not text.strip() for text in self.query_required + self.query_forbidden):
            raise ValueError("검색어 진단 표현은 비어 있을 수 없습니다.")
        return self

    def answer_case(self) -> QualityCase:
        return QualityCase.model_validate(
            {
                key: value
                for key, value in self.model_dump().items()
                if key in QualityCase.model_fields
            }
        )


class SearchDataset(StrictModel):
    schema_version: Literal[1]
    fixture_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    rubric: Rubric
    cases: list[SearchCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def common_contract(self) -> Self:
        self.answer_dataset()
        return self

    def answer_dataset(self) -> Dataset:
        return Dataset(
            schema_version=1,
            fixture_id=self.fixture_id,
            description=self.description,
            scope=self.scope,
            rubric=self.rubric,
            cases=[case.answer_case() for case in self.cases],
        )


def load_search_dataset(path: Path = DEFAULT_SEARCH_DATASET) -> SearchDataset:
    try:
        return SearchDataset.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise ValueError("검색 평가 자료를 읽거나 형식을 검증하지 못했습니다.") from error
