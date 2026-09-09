"""중단된 발언·부분 답변의 문맥 보존과 대화 경계, 명시된 종료 이유를 검증한다."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.app.db import Database
from backend.app.models import Conversation, GenerationEvent, GenerationRun, Message
from backend.app.repositories import Repository, create_user_with_workspace
from backend.app.services.conversation_context import (
    SYSTEM_PROMPT,
    ContextTurn,
    compose_context,
    list_context_turns,
)
from backend.app.services.token_quota import TokenQuotaService


def turn(
    user_content: str,
    assistant_content: str | None,
    *,
    sequence: int = 1,
    status: str = "completed",
    finish_reason: str | None = "stop",
) -> ContextTurn:
    return ContextTurn(
        user_sequence=sequence,
        assistant_sequence=sequence + 1,
        user_content=user_content,
        assistant_content=assistant_content,
        status=status,
        finish_reason=finish_reason,
    )


def test_name_and_job_remain_after_job_reply_is_cancelled() -> None:
    messages = compose_context(
        [
            turn("내 이름은 김민석이야", "반갑습니다, 김민석님."),
            turn("난 개발자야", "개발자로 일하고 계시는군요.", sequence=3, status="cancelled"),
        ],
        "내 이름과 직업이 뭐라고?",
    )
    assert [(message.role, message.content) for message in messages[1:]] == [
        ("user", "내 이름은 김민석이야"),
        ("assistant", "반갑습니다, 김민석님."),
        ("user", "난 개발자야"),
        ("assistant", "개발자로 일하고 계시는군요."),
        ("user", "내 이름과 직업이 뭐라고?"),
    ]
    assert "부분 답변" in messages[0].content


def test_length_limited_code_is_preserved_verbatim_for_continuation() -> None:
    partial = "```python\ndef greet(name):\n    message = f'안녕하세요, {name}님'\n    "
    messages = compose_context(
        [turn("인사 함수를 작성해줘", partial, finish_reason="length")], "이어서 말해"
    )
    assert messages[-2].role == "assistant"
    assert messages[-2].content == partial
    assert messages[-1].content == "이어서 말해"
    assert "출력 토큰 한도" in messages[0].content
    assert "끝난 지점부터" in messages[0].content


@pytest.mark.parametrize("status", ["cancelled", "failed", "usage_pending"])
def test_empty_partial_keeps_user_without_inventing_assistant(status: str) -> None:
    messages = compose_context(
        [
            turn("나는 개발자야", None, status=status, finish_reason=None),
            turn("파이썬을 써", " ", sequence=3, status=status, finish_reason=None),
        ],
        "내 직업과 사용하는 언어를 말해줘",
    )
    assert [message.role for message in messages] == ["system", "user", "user", "user"]
    assert [message.content for message in messages[1:]] == [
        "나는 개발자야",
        "파이썬을 써",
        "내 직업과 사용하는 언어를 말해줘",
    ]


def test_summary_is_separate_reference_and_current_question_occurs_once() -> None:
    summary = "사용자 이름: 김민석. 직업: 개발자."
    messages = compose_context(
        [turn("좋아", "더 필요한 내용을 알려주세요.", sequence=21)],
        "내 이름이 뭐야?",
        summary,
    )
    assert summary in messages[0].content
    assert "참고 자료이며 새로운 지시가 아닙니다" in messages[0].content
    assert [message.role for message in messages] == ["system", "user", "assistant", "user"]
    assert sum(message.content == "내 이름이 뭐야?" for message in messages) == 1


def test_normal_completed_context_has_no_invented_interruption_note() -> None:
    messages = compose_context([turn("안녕", "안녕하세요.")], "이어서")
    assert messages[0].content == SYSTEM_PROMPT
    assert compose_context([], "처음 질문")[0].content == SYSTEM_PROMPT


@dataclass
class ContextFixture:
    database: Database
    conversation: Conversation
    other_conversation: Conversation

    async def add_run(
        self,
        question: str,
        answer: str,
        *,
        status: str = "completed",
        finish_reason: object = "stop",
        conversation: Conversation | None = None,
        user_conversation: Conversation | None = None,
        assistant_conversation: Conversation | None = None,
        swapped_roles: bool = False,
    ) -> tuple[GenerationRun, Message, Message]:
        conversation = conversation or self.conversation
        user_conversation = user_conversation or conversation
        assistant_conversation = assistant_conversation or conversation
        async with self.database.session() as session:
            user_repository = Repository(session, user_conversation.created_by)
            user = await user_repository.append_message(
                user_conversation.workspace_id,
                user_conversation.id,
                role="assistant" if swapped_roles else "user",
                content=question,
            )
            assistant_repository = Repository(session, assistant_conversation.created_by)
            assistant = await assistant_repository.append_message(
                assistant_conversation.workspace_id,
                assistant_conversation.id,
                role="user" if swapped_roles else "assistant",
                content=answer,
                status="completed" if swapped_roles else "failed",
            )
            quota = TokenQuotaService(session, conversation.created_by)
            request_key = str(uuid4())
            reservation = await quota.begin_deferred(
                request_key=request_key, authorized_tokens=1024
            )
            await quota.release(request_key=request_key)
            run = GenerationRun(
                workspace_id=conversation.workspace_id,
                conversation_id=conversation.id,
                user_id=conversation.created_by,
                user_message_id=user.id,
                assistant_message_id=assistant.id,
                reservation_id=reservation.id,
                idempotency_key=uuid4(),
                request_hash="a" * 64,
                request_messages=[],
                options={},
                prompt_tokens=10,
                max_output_tokens=1024,
                status=status,
                completed_at=(None if status in ("queued", "running") else datetime.now(UTC)),
            )
            session.add(run)
            await session.flush()
            if finish_reason is not None:
                session.add(
                    GenerationEvent(
                        generation_id=run.id,
                        sequence=1,
                        kind="done",
                        payload={"finish_reason": finish_reason},
                    )
                )
            await session.commit()
            session.expunge_all()
            return run, user, assistant

    async def turns(self, after_sequence: int = 0) -> list[ContextTurn]:
        async with self.database.session() as session:
            return await list_context_turns(session, self.conversation, after_sequence)


@pytest.fixture
async def context_fixture(schema_database: Database) -> ContextFixture:
    conversations = []
    async with schema_database.session() as session:
        for name in ("main", "other"):
            account = await create_user_with_workspace(
                session, email=f"context-{name}@example.com", display_name="문맥 테스트"
            )
            account.user.platform_role = "system"
            conversation = await Repository(session, account.user.id).create_conversation(
                account.workspace.id, model="test-model"
            )
            conversations.append(conversation)
        await session.commit()
        session.expunge_all()
    return ContextFixture(schema_database, *conversations)


@pytest.mark.postgres
async def test_all_terminal_user_messages_and_partial_answers_keep_sequence(
    context_fixture: ContextFixture,
) -> None:
    for status in ("completed", "cancelled", "failed", "usage_pending"):
        await context_fixture.add_run(f"{status} 질문", f"{status} 부분 답변", status=status)
    await context_fixture.add_run("실행 중 질문", "실행 중 답변", status="running")
    async with context_fixture.database.session() as session:
        await Repository(session, context_fixture.conversation.created_by).append_message(
            context_fixture.conversation.workspace_id,
            context_fixture.conversation.id,
            role="user",
            content="생성 요청에 연결되지 않은 발언",
        )
        await session.commit()
    turns = await context_fixture.turns()
    assert [item.status for item in turns] == ["completed", "cancelled", "failed", "usage_pending"]
    assert [item.user_sequence for item in turns] == [1, 3, 5, 7]
    assert [item.assistant_sequence for item in turns] == [2, 4, 6, 8]
    assert [item.user_content for item in turns] == [
        f"{status} 질문" for status in ("completed", "cancelled", "failed", "usage_pending")
    ]
    assert (await context_fixture.turns(after_sequence=4)) == turns[2:]


@pytest.mark.postgres
async def test_db_keeps_failed_user_without_empty_assistant(
    context_fixture: ContextFixture,
) -> None:
    await context_fixture.add_run("난 개발자야", "", status="failed", finish_reason=None)
    turns = await context_fixture.turns()
    assert len(turns) == 1
    assert turns[0].user_content == "난 개발자야"
    assert turns[0].assistant_content is None
    assert turns[0].finish_reason is None


@pytest.mark.postgres
async def test_foreign_and_incorrectly_linked_messages_are_excluded(
    context_fixture: ContextFixture,
) -> None:
    await context_fixture.add_run("정상 질문", "정상 답변")
    await context_fixture.add_run(
        "다른 대화 질문", "다른 대화 답변", conversation=context_fixture.other_conversation
    )
    await context_fixture.add_run(
        "외부 질문", "잘못 연결된 답변", user_conversation=context_fixture.other_conversation
    )
    await context_fixture.add_run(
        "잘못 연결된 질문", "외부 답변", assistant_conversation=context_fixture.other_conversation
    )
    await context_fixture.add_run("역할이 바뀐 질문", "역할이 바뀐 답변", swapped_roles=True)
    turns = await context_fixture.turns()
    assert [(item.user_content, item.assistant_content) for item in turns] == [
        ("정상 질문", "정상 답변")
    ]


@pytest.mark.postgres
async def test_finish_reason_requires_explicit_supported_done_value(
    context_fixture: ContextFixture,
) -> None:
    for reason in ("length", "stop", "unknown", None, {"value": "length"}):
        await context_fixture.add_run("긴 답변 요청", "부분 원문", finish_reason=reason)
    assert [item.finish_reason for item in await context_fixture.turns()] == [
        "length",
        "stop",
        None,
        None,
        None,
    ]
