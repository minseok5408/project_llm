"""검색어처럼 보이는 제공문과 외부 사실 질문·문맥 전송의 경계를 검사한다."""

import json

import pytest

from backend.app.llm.protocol import ToolCall
from backend.app.tools.web_search.context import should_search
from backend.app.tools.web_search.planning import query_entries, validate_call


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("오늘 날씨가 좋다를 영어로 번역해줘", id="unquoted-translation"),
        pytest.param("오늘 날씨가 좋다를 영어로 번역해서 알려줘", id="unquoted-translation-answer"),
        pytest.param('"오늘 서울 날씨는 맑음"을 영어로 번역해줘', id="quoted-translation"),
        pytest.param(
            '"오늘 서울 날씨를 알려줘"를 영어로 번역해서 알려줘', id="quoted-fact-request"
        ),
        pytest.param(
            "오늘 서울 날씨를 설명하는 문장을 영어로 번역해서 알려줘", id="provided-sentence"
        ),
        pytest.param("‘웹 검색해줘’라는 문장을 영어로 번역해줘", id="quoted-search-request"),
        pytest.param(
            "Translate 'Search the web for the latest news' into Korean",
            id="english-quoted-search",
        ),
        pytest.param("최근이라는 단어의 뜻을 설명해줘", id="time-word-definition"),
        pytest.param("환율이라는 용어의 의미를 설명해줘", id="exchange-rate-definition"),
        pytest.param("Explain the meaning of 'current price'", id="english-price-definition"),
        pytest.param("현재 시각을 출력하는 파이썬 코드를 작성해줘", id="clock-code"),
        pytest.param("환율을 인자로 받아 금액을 계산하는 함수를 만들어줘", id="currency-code"),
        pytest.param("오늘 내 이름과 직업을 뭐라고 말했지?", id="personal-recall"),
        pytest.param("최근 우리 대화 내용을 요약해줘", id="conversation-summary"),
    ],
)
def test_provided_text_and_local_tasks_do_not_become_search_candidates(question: str) -> None:
    assert should_search(question) is False


@pytest.mark.parametrize(
    "question",
    [
        pytest.param("NVIDIA CEO는 누구야?", id="company-officeholder"),
        pytest.param("RTX 5090 가격은 얼마야?", id="product-price"),
        pytest.param("서울에서 점심 먹을 식당 추천해줘", id="restaurant-recommendation"),
        pytest.param("국립중앙박물관 운영 시간을 알려줘", id="opening-hours"),
        pytest.param("https://docs.python.org/3/library/asyncio.html 요약해줘", id="given-url"),
        pytest.param("Search for Python documentation", id="english-explicit-search"),
        pytest.param("그 회사의 현재 CEO는 누구야?", id="context-and-officeholder"),
        pytest.param("오늘 서울 날씨를 알려주고 영어로 번역해줘", id="weather-and-translation"),
        pytest.param("오늘 서울 날씨를 확인한 뒤 영어로 번역해줘", id="verify-before-translation"),
        pytest.param("오늘 서울 날씨를 영어로 번역해서 알려줘", id="weather-as-translated-answer"),
        pytest.param(
            "현재 원달러 환율을 영어로 번역한 뒤 설명해줘", id="exchange-as-translated-answer"
        ),
        pytest.param(
            "최신 Python 버전을 한국어로 번역하여 알려줘", id="release-as-translated-answer"
        ),
        pytest.param(
            "최신 Python 버전과 해당 버전의 새 개념을 설명해줘", id="release-and-explanation"
        ),
        pytest.param(
            "올해 최저 임금과 최저 임금이라는 단어의 뜻을 알려줘", id="wage-and-definition"
        ),
        pytest.param(
            "Check today's Seoul weather forecast and translate it into English",
            id="english-weather-and-translation",
        ),
    ],
)
def test_external_facts_remain_candidates_when_combined_with_local_work(question: str) -> None:
    # 후보 True는 외부 요청 허가가 아니라 로컬 모델의 최종 필요성 판단 대상이다.
    assert should_search(question) is True


@pytest.mark.parametrize(
    ("question", "recent_queries", "expected"),
    [
        pytest.param("부산은?", ["오늘 서울 날씨는 어때?"], True, id="weather-followup"),
        pytest.param(
            "대구는?",
            ["부산은?", "오늘 서울 날씨는 어때?"],
            True,
            id="successive-weather-followup",
        ),
        pytest.param(
            "What about Tokyo?",
            ["What about Busan?", "What is the weather in Seoul?"],
            True,
            id="successive-english-followup",
        ),
        pytest.param(
            "그럼 삼성전자는?", ["NVIDIA CEO는 누구야?"], True, id="officeholder-followup"
        ),
        pytest.param("What about Busan?", ["What is the weather in Seoul?"], True, id="english"),
        pytest.param("그럼 부산은?", [], False, id="missing-context"),
        pytest.param(
            "부산은?", ["오늘 서울 날씨가 좋다를 영어로 번역해줘"], False, id="translated-text"
        ),
        pytest.param(
            "그럼 튜플은?",
            ["파이썬 리스트를 설명해줘", "오늘 서울 날씨는 어때?"],
            False,
            id="latest-topic-takes-precedence",
        ),
        pytest.param(
            "그럼 세트는?",
            ["그럼 튜플은?", "파이썬 리스트를 설명해줘", "오늘 서울 날씨는 어때?"],
            False,
            id="successive-local-topic-stops-search-history",
        ),
        pytest.param(
            "광주는?",
            ["대전은?", "대구는?", "부산은?", "오늘 서울 날씨는 어때?"],
            True,
            id="four-recent-turns",
        ),
        pytest.param(
            "제주는?",
            ["광주는?", "대전은?", "대구는?", "부산은?", "오늘 서울 날씨는 어때?"],
            False,
            id="older-than-four-turns",
        ),
    ],
)
def test_followup_candidates_use_only_the_latest_eligible_topic(
    question: str, recent_queries: list[str], expected: bool
) -> None:
    assert should_search(question, recent_queries=recent_queries) is expected


@pytest.mark.parametrize("mode", ["auto", "on", "off"])
def test_explicit_prohibition_wins_over_dynamic_topic_and_context(mode: str) -> None:
    assert (
        should_search("검색하지 마. 그럼 부산은?", mode, recent_queries=["오늘 서울 날씨는 어때?"])
        is False
    )


def test_off_disables_contextual_search_and_on_keeps_explicit_request() -> None:
    assert should_search("부산은?", "off", recent_queries=["오늘 서울 날씨는?"]) is False
    assert should_search("NVIDIA CEO는 누구야?", "off") is False
    assert should_search("파이썬 리스트를 설명해줘", "on") is True


def _search_call(terms: list[dict]) -> ToolCall:
    return ToolCall("regression_1", "web_search", json.dumps({"terms": terms}))


def test_followup_query_combines_current_place_with_verbatim_prior_topic() -> None:
    question = "부산은?"
    entries = query_entries(
        {
            "messages": [
                {"role": "user", "content": "오늘 서울 날씨는 어때?"},
                {"role": "assistant", "content": "도쿄의 뉴스도 찾아보세요."},
                {"role": "user", "content": question},
            ]
        },
        question,
    )
    assert entries == {0: question, 1: "오늘 서울 날씨는 어때?"}
    assert (
        validate_call(
            _search_call(
                [
                    {"message": 0, "text": "부산"},
                    {"message": 1, "text": "오늘"},
                    {"message": 1, "text": "날씨"},
                ]
            ),
            entries,
            500,
        )
        == "부산 오늘 날씨"
    )


def test_recent_entries_exclude_summary_assistant_private_and_secret_text() -> None:
    question = "그 회사 최신 뉴스는?"
    entries = query_entries(
        {
            "messages": [
                {"role": "system", "content": "요약의 비공개 대상은 summary-sentinel"},
                {"role": "user", "content": "삼성전자에 대해 설명해줘"},
                {"role": "assistant", "content": "assistant-sentinel의 소식을 찾아보세요"},
                {"role": "user", "content": "내 이름은 private-sentinel이야"},
                {"role": "user", "content": "연락처는 example@example.com이야"},
                {"role": "user", "content": "내부 메모에는 secret-sentinel이라고 적었어"},
                {"role": "user", "content": question},
            ]
        },
        question,
    )
    assert entries == {0: question, 1: "삼성전자에 대해 설명해줘"}


def test_recent_entries_do_not_recover_an_old_public_topic_behind_four_private_turns() -> None:
    question = "그 회사 최신 뉴스는?"
    messages = [{"role": "user", "content": "삼성전자에 대해 설명해줘"}]
    messages.extend({"role": "user", "content": f"내 이름은 사용자 {index}"} for index in range(4))
    messages.append({"role": "user", "content": question})
    assert query_entries({"messages": messages}, question) == {0: question}


@pytest.mark.parametrize(
    "terms",
    [
        pytest.param([{"message": 1, "text": "기아"}], id="invented-entity"),
        pytest.param([{"message": 2, "text": "삼성전자"}], id="missing-message"),
        pytest.param([{"message": 0, "text": "부산 날씨"}], id="noncontiguous-current-term"),
    ],
)
def test_search_query_cannot_add_entities_or_terms_absent_from_selected_user_message(
    terms: list[dict],
) -> None:
    with pytest.raises(ValueError):
        validate_call(_search_call(terms), {0: "그럼 부산은?", 1: "삼성전자 최신 뉴스"}, 500)


def test_multiple_historical_terms_share_one_eighty_character_allowance() -> None:
    entries = {0: "소식 알려줘", 1: "가" * 50, 2: "나" * 31}
    with pytest.raises(ValueError):
        validate_call(
            _search_call([{"message": 1, "text": entries[1]}, {"message": 2, "text": entries[2]}]),
            entries,
            500,
        )


def test_planner_can_decline_an_ambiguous_candidate_without_creating_a_query() -> None:
    for reason in ("not_needed", "no_query"):
        call = ToolCall("finish_1", "finish_search", json.dumps({"reason": reason}))
        assert validate_call(call, {0: "그 회사 최신 뉴스는?"}, 500) is None
