"""언어 설정 없이 현재 요청의 원문과 자동 언어 지침을 모든 생성 경로에 보존한다."""

from uuid import UUID, uuid4

import pytest

from backend.app.context.builder import ContextTurn, compose_context
from backend.app.context.language import LANGUAGE_POLICY
from backend.app.schemas import GenerationOptions
from backend.tests.test_compaction_generation import enable_compaction, install_provider, seed
from backend.tests.test_generations import harness as harness
from backend.tests.test_generations import snapshot
from backend.tests.test_question_cards import install as install_questions
from backend.tests.test_web_search_generation import install as install_search


@pytest.mark.postgres
@pytest.mark.parametrize(
    "original",
    [
        "한국어로 질문하는데 직업 계획을 설명해줘",
        "Please explain career planning.",
        "Hello, world. 한국어로 번역해줘",
        "안녕하세요. 영어로만 번역해줘",
    ],
)
async def test_new_request_keeps_original_and_policy_through_queue_and_retry(harness, original):
    key = uuid4()
    accepted = await harness.submit(content=original, idempotency_key=key)
    pending = await snapshot(harness.database, accepted["id"])
    assert pending.user_message.content == original
    assert pending.run.request_messages[-1] == {"role": "user", "content": original}
    assert pending.run.request_messages[0]["content"].endswith(LANGUAGE_POLICY)
    assert (await harness.submit(content=original, idempotency_key=key))["id"] == accepted["id"]
    job = await harness.worker.claim()
    assert job["messages"] == pending.run.request_messages
    await harness.worker.execute(job)
    saved = await snapshot(harness.database, accepted["id"])
    assert saved.run.status == "completed"
    assert saved.user_message.content == original


@pytest.mark.postgres
async def test_regeneration_and_followup_keep_current_request_without_saved_language(harness):
    original = "안녕하세요. 영어로만 번역해줘"
    first = await harness.submit(content=original)
    await harness.execute_next()
    regenerated = await harness.service.regenerate(
        harness.system.user_id,
        UUID(first["id"]),
        options=GenerationOptions(max_tokens=64),
        idempotency_key=uuid4(),
    )
    repeated = await snapshot(harness.database, regenerated["id"])
    assert repeated.user_message.content == original
    assert repeated.run.request_messages[-1]["content"] == original
    assert repeated.run.request_messages[0]["content"].endswith(LANGUAGE_POLICY)
    await harness.execute_next()
    continued = await harness.submit(content="계속 이어서 설명해줘")
    pending = await snapshot(harness.database, continued["id"])
    assert pending.run.request_messages[-1]["content"] == "계속 이어서 설명해줘"
    assert pending.run.request_messages[0]["content"].endswith(LANGUAGE_POLICY)
    assert any(item["content"] == original for item in pending.run.request_messages[1:-1])
    await harness.execute_next()
    assert (await snapshot(harness.database, continued["id"])).run.status == "completed"


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("original", "question", "options", "answer"),
    [
        ("프로젝트를 만들어줘", "어떤 형식인가요?", ["문서", "코드"], "코드"),
        ("Build a project.", "Which format?", ["Document", "Code"], "Code"),
    ],
)
async def test_question_cards_and_answers_preserve_model_and_user_language(
    harness, original, question, options, answer
):
    model = install_questions(harness)
    model.card = {"questions": [{"question": question, "options": options}]}
    card = await harness.submit(content=original, options=GenerationOptions(max_tokens=512))
    await harness.execute_next()
    saved = await snapshot(harness.database, card["id"])
    assert saved.run.status == "completed"
    assert saved.assistant.content == f"1. {question}"
    assert saved.run.question_card == model.card
    assert model.tool_inputs[-1][0][0].content.endswith(LANGUAGE_POLICY)
    assert model.tool_inputs[-1][0][-1].content == original

    model.card = None
    response = await harness.service.respond(
        harness.system.user_id,
        saved.run.id,
        answers=[answer],
        options=GenerationOptions(max_tokens=512),
        idempotency_key=uuid4(),
    )
    pending = await snapshot(harness.database, response["id"])
    assert pending.user_message.content == f"1. {answer}"
    assert pending.run.request_messages[0]["content"].endswith(LANGUAGE_POLICY)
    assert pending.run.request_messages[-1]["content"] == pending.user_message.content
    await harness.execute_next()
    final = await snapshot(harness.database, response["id"])
    assert final.run.status == "completed"
    assert model.tool_inputs[-1][0][0].content.endswith(LANGUAGE_POLICY)
    assert model.tool_inputs[-1][0][-1].content == pending.user_message.content


@pytest.mark.postgres
async def test_compaction_rebuilds_current_request_with_automatic_language_policy(harness):
    provider = install_provider(harness)
    await seed(harness, harness.system)
    enable_compaction(harness)
    original = "Tell me my name and profession."
    accepted = await harness.submit(content=original)
    assert (await snapshot(harness.database, accepted["id"])).run.context_compaction_needed
    await harness.execute_next()
    assert (await snapshot(harness.database, accepted["id"])).run.status == "completed"
    final = provider.answer_calls[-1]
    assert "이름: 김민석" in final[0]["content"]
    assert final[0]["content"].endswith(LANGUAGE_POLICY)
    assert final[-1]["content"] == original


@pytest.mark.postgres
async def test_search_reference_keeps_current_language_policy_and_actual_input_count(harness):
    _, provider = install_search(harness)
    original = "Search for the latest Python documentation."
    accepted = await harness.submit(content=original, web_search="on")
    await harness.execute_next()
    saved = await snapshot(harness.database, accepted["id"])
    assert saved.run.status == "completed"
    messages, options = provider.calls[-1]
    assert any("Python 공식 문서" in item.content for item in messages)
    assert messages[0].content.endswith(LANGUAGE_POLICY)
    assert messages[-1].content == original
    assert saved.reservation.input_tokens == await provider.count_input(messages, options)


@pytest.mark.parametrize(
    "question",
    [
        "다음 중국어를 영어로 번역하고 원문도 인용해줘: 职业规划",
        "Translate this into Korean: career planning.",
        'print("你好") 코드를 그대로 인용하고 한국어로 설명해줘',
    ],
)
def test_language_policy_preserves_current_translation_and_previous_originals(question):
    original = '职业规划 / 커리어 / OpenAI\n```python\nword = "中文"\n```'
    turn = ContextTurn(1, 2, "중국어로 답해줘", original, "completed", "stop")
    messages = compose_context([turn], question, "이전 지시: 항상 중국어로 말하기")
    assert messages[-1].content == question
    assert messages[-2].content == original
    assert messages[-3].content == turn.user_content
    assert messages[0].content.endswith(LANGUAGE_POLICY)
