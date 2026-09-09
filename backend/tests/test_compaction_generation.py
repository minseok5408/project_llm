"""실제 PostgreSQL에서 자동 요약의 재사용·원문 보존·비용 분리·중단을 검증한다."""

import asyncio
import json
from uuid import UUID

import pytest
from sqlalchemy import select

from backend.app.models import Conversation, ConversationCompaction, GenerationRun, Message
from backend.app.providers import ProviderDelta
from backend.app.services.compaction_service import latest_summary
from backend.app.services.context_compaction import SUMMARY_PROMPT
from backend.tests.test_generations import Account, Harness, balance, snapshot
from backend.tests.test_generations import harness as harness

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class CompactionProvider:
    """문맥 길이와 요약 종료 상태를 통제하면서 공급자의 실제 사용량 계약을 유지한다."""

    def __init__(self):
        self.summary_mode = "normal"
        self.answer_calls = []
        self.summary_calls = []
        self.summary_started = asyncio.Event()
        self.summary_release = asyncio.Event()
        self.summary_closed = asyncio.Event()

    @staticmethod
    def is_summary(messages) -> bool:
        return messages[0].content == SUMMARY_PROMPT

    async def count_input(self, messages, options):
        if self.is_summary(messages):
            records = [json.loads(message.content) for message in messages[1:]]
            return (
                100
                + 300 * sum("user" in record for record in records)
                + 40 * sum("previous_summary" in record for record in records)
            )
        return (
            40
            + 160 * sum(message.role == "assistant" for message in messages)
            + 20 * sum(message.role == "user" for message in messages)
            + (40 if "<conversation_summary>" in messages[0].content else 0)
        )

    async def stream(self, messages, options):
        prompt_tokens = await self.count_input(messages, options)
        if self.is_summary(messages):
            self.summary_calls.append([message.model_dump() for message in messages])
            try:
                if self.summary_mode == "blocked":
                    yield ProviderDelta(text="아직 작성 중인 요약", received_output_tokens=1)
                    self.summary_started.set()
                    await self.summary_release.wait()
                content = (
                    " "
                    if self.summary_mode == "blank"
                    else "이름: 김민석. 직업: 개발자. 언어: Python."
                )
                yield ProviderDelta(text=content, received_output_tokens=12)
                if self.summary_mode == "missing_usage":
                    return
                yield ProviderDelta(
                    input_tokens=(
                        prompt_tokens + 1
                        if self.summary_mode == "wrong_input_usage"
                        else prompt_tokens
                    ),
                    output_tokens=(
                        options.max_tokens + 1 if self.summary_mode == "excess_output_usage" else 12
                    ),
                    received_output_tokens=12,
                    final=True,
                    finish_reason="length" if self.summary_mode == "length" else "stop",
                )
            finally:
                self.summary_closed.set()
            return
        self.answer_calls.append([message.model_dump() for message in messages])
        yield ProviderDelta(text=f"정상 답변 {len(self.answer_calls)}", received_output_tokens=5)
        yield ProviderDelta(
            input_tokens=prompt_tokens,
            output_tokens=5,
            received_output_tokens=5,
            final=True,
            finish_reason="stop",
        )


def install_provider(harness: Harness) -> CompactionProvider:
    provider = CompactionProvider()
    harness.provider = harness.service.provider = provider
    return provider


def enable_compaction(harness: Harness) -> None:
    harness.settings = harness.service.settings = harness.settings.model_copy(
        update={
            "llm_context_window": 1_024,
            "llm_compaction_trigger_ratio": 0.75,
            "llm_compaction_target_ratio": 0.55,
            "llm_compaction_keep_turns": 2,
            "llm_compaction_max_tokens": 128,
        }
    )


async def seed(harness: Harness, account: Account, count: int = 4) -> list[UUID]:
    questions = [
        "내 이름은 김민석이야",
        "난 개발자야",
        "Python을 주로 써",
        "간단한 함수를 만들어줘",
    ]
    ids = []
    for index in range(count):
        request = await harness.submit(
            account, content=questions[index] if index < len(questions) else f"다음 질문 {index}"
        )
        ids.append(await harness.execute_next())
        assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    return ids


async def attempts(harness: Harness, account: Account) -> list[ConversationCompaction]:
    async with harness.database.session() as session:
        rows = list(
            (
                await session.scalars(
                    select(ConversationCompaction)
                    .where(ConversationCompaction.conversation_id == account.conversation_id)
                    .order_by(ConversationCompaction.created_at, ConversationCompaction.id)
                )
            ).all()
        )
        session.expunge_all()
        return rows


async def message_rows(harness: Harness, account: Account) -> list[tuple]:
    async with harness.database.session() as session:
        return [
            tuple(row)
            for row in (
                await session.execute(
                    select(Message.id, Message.sequence, Message.role, Message.content)
                    .where(Message.conversation_id == account.conversation_id)
                    .order_by(Message.sequence)
                )
            ).all()
        ]


async def checkpoint(harness: Harness, account: Account) -> tuple[str | None, int]:
    async with harness.database.session() as session:
        conversation = await session.get(Conversation, account.conversation_id)
        return await latest_summary(session, conversation)


async def test_automatic_compaction_keeps_originals_and_reuses_summary_once(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    await seed(harness, harness.system)
    originals = await message_rows(harness, harness.system)
    enable_compaction(harness)
    request = await harness.submit(content="내 이름과 직업을 알려줘")
    assert (await snapshot(harness.database, request["id"])).run.context_compaction_needed
    await harness.execute_next()

    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.system)
    assert result.run.status == "completed"
    assert len(summaries) == len(provider.summary_calls) == 1
    assert summaries[0].status == "completed"
    assert summaries[0].through_sequence == 4
    assert summaries[0].input_tokens == 700
    assert summaries[0].output_tokens == 12
    assert (await message_rows(harness, harness.system))[: len(originals)] == originals
    context = provider.answer_calls[-1]
    assert "이름: 김민석. 직업: 개발자." in context[0]["content"]
    assert [message["content"] for message in context[1:-1]] == [row[3] for row in originals[4:]]
    assert context[-1]["content"] == "내 이름과 직업을 알려줘"
    assert any(event.payload.get("stage") == "compacting" for event in result.events)
    assert any(event.payload.get("context_compacted") is True for event in result.events)

    following = await harness.submit(content="사용하는 언어도 알려줘")
    assert not (await snapshot(harness.database, following["id"])).run.context_compaction_needed
    await harness.execute_next()
    assert len(await attempts(harness, harness.system)) == 1
    assert len(provider.summary_calls) == 1
    assert provider.answer_calls[-1][0]["content"].count("이름: 김민석.") == 1
    assert all(
        message["content"] != "내 이름은 김민석이야" for message in provider.answer_calls[-1][1:]
    )


async def test_member_pays_only_answer_usage_and_not_summary_work(harness: Harness) -> None:
    install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    before = await balance(harness.database, harness.member)
    request = await harness.submit(harness.member, content="기억한 내용 정리해줘")
    admitted = await balance(harness.database, harness.member)
    assert admitted.used_tokens == before.used_tokens
    assert admitted.reserved_tokens == 0
    await harness.execute_next()
    result = await snapshot(harness.database, request["id"])
    after = await balance(harness.database, harness.member)
    summaries = await attempts(harness, harness.member)
    assert result.run.status == "completed"
    assert result.reservation.input_tokens == 460
    assert result.reservation.output_tokens == 5
    assert after.used_tokens - before.used_tokens == 465
    assert summaries[0].input_tokens + summaries[0].output_tokens == 712
    assert after.reserved_tokens == 0


async def test_cancel_during_summary_closes_stream_waives_request_and_allows_next(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    provider.summary_mode = "blocked"
    before = await balance(harness.database, harness.member)
    answer_count = len(provider.answer_calls)
    request = await harness.submit(harness.member, content="요약 중 중단할 질문")
    job = await harness.worker.claim()
    running = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(provider.summary_started.wait(), timeout=3)
        await harness.service.cancel(harness.member.user_id, UUID(request["id"]))
        await asyncio.wait_for(running, timeout=3)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert provider.summary_closed.is_set()
    assert not provider.summary_release.is_set()
    assert len(provider.answer_calls) == answer_count
    assert result.run.status == result.assistant.status == "cancelled"
    assert result.assistant.content == ""
    assert result.reservation.usage_basis == "waived"
    assert result.reservation.input_tokens == result.reservation.output_tokens == 0
    assert (await balance(harness.database, harness.member)).used_tokens == before.used_tokens
    assert len(summaries) == 1
    assert summaries[0].status == "cancelled"
    assert summaries[0].content is None
    assert summaries[0].usage_basis == "received"
    assert summaries[0].output_tokens == 1
    assert await checkpoint(harness, harness.member) == (None, 0)
    following = await harness.submit(harness.member, content="다시 질문")
    assert following["status"] == "queued"
    await harness.service.cancel(harness.member.user_id, UUID(following["id"]))


async def test_worker_shutdown_during_summary_keeps_no_checkpoint_or_member_charge(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    provider.summary_mode = "blocked"
    before = await balance(harness.database, harness.member)
    answer_count = len(provider.answer_calls)
    request = await harness.submit(harness.member, content="서버 종료 시 요약 상태 검증")
    job = await harness.worker.claim()
    running = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(provider.summary_started.wait(), timeout=3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=3)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert provider.summary_closed.is_set()
    assert len(provider.answer_calls) == answer_count
    assert result.run.status == "failed"
    assert result.reservation.status == "released"
    assert result.reservation.usage_basis == "waived"
    assert (await balance(harness.database, harness.member)).used_tokens == before.used_tokens
    assert len(summaries) == 1
    assert summaries[0].status == "failed"
    assert summaries[0].content is None
    assert summaries[0].error_code == "worker_stopped"
    assert summaries[0].usage_basis == "received"
    assert await checkpoint(harness, harness.member) == (None, 0)
    following = await harness.submit(harness.member, content="재시작 이후 질문")
    assert following["status"] == "queued"
    await harness.service.cancel(harness.member.user_id, UUID(following["id"]))


async def test_leader_connection_loss_during_summary_closes_stream_without_charging(
    harness: Harness,
) -> None:
    class LeaderConnection:
        closed = False

        def is_closed(self):
            return self.closed

    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    provider.summary_mode = "blocked"
    before = await balance(harness.database, harness.member)
    answer_count = len(provider.answer_calls)
    request = await harness.submit(harness.member, content="실행자 잠금 연결 종료 검증")
    job = await harness.worker.claim()
    connection = LeaderConnection()
    harness.worker.connection = connection
    running = asyncio.create_task(harness.worker.execute(job))
    try:
        await asyncio.wait_for(provider.summary_started.wait(), timeout=3)
        connection.closed = True
        await asyncio.wait_for(running, timeout=3)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        harness.worker.connection = None
    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert provider.summary_closed.is_set()
    assert len(provider.answer_calls) == answer_count
    assert result.run.status == "failed"
    assert result.reservation.status == "released"
    assert result.reservation.usage_basis == "waived"
    assert (await balance(harness.database, harness.member)).used_tokens == before.used_tokens
    assert len(summaries) == 1
    assert summaries[0].status == "failed"
    assert summaries[0].content is None
    assert summaries[0].usage_basis == "received"
    assert await checkpoint(harness, harness.member) == (None, 0)


@pytest.mark.parametrize("failure", ["length", "blank"])
async def test_incomplete_summary_cannot_replace_last_valid_checkpoint(
    harness: Harness, failure: str
) -> None:
    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    enable_compaction(harness)
    for content in ("처음 요약", "그다음 질문"):
        await harness.submit(harness.member, content=content)
        await harness.execute_next()
    saved = await checkpoint(harness, harness.member)
    assert saved[1] == 4
    provider.summary_mode = failure
    answer_count = len(provider.answer_calls)
    before = await balance(harness.database, harness.member)
    request = await harness.submit(harness.member, content="다시 요약해야 하는 질문")
    await harness.execute_next()
    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert result.run.status == "failed"
    assert result.assistant.content == ""
    assert result.reservation.status == "released"
    assert len(provider.answer_calls) == answer_count
    assert (await balance(harness.database, harness.member)).used_tokens == before.used_tokens
    assert await checkpoint(harness, harness.member) == saved
    assert [summary.status for summary in summaries] == ["completed", "failed"]
    assert summaries[-1].content is None
    assert summaries[-1].error_code == "summary_incomplete"
    following = await harness.submit(harness.member, content="요약 실패 뒤 다시 질문")
    assert following["status"] == "queued"
    await harness.service.cancel(harness.member.user_id, UUID(following["id"]))


async def test_long_history_rolls_through_batches_without_skipping_or_repeating_turns(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    await seed(harness, harness.system, count=8)
    originals = await message_rows(harness, harness.system)
    enable_compaction(harness)
    request = await harness.submit(content="모든 이전 내용을 참고해 답해줘")
    await harness.execute_next()
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    summaries = await attempts(harness, harness.system)
    assert [summary.through_sequence for summary in summaries] == [4, 8, 12]
    assert all(summary.status == "completed" for summary in summaries)
    all_records = [
        json.loads(message["content"])
        for messages in provider.summary_calls
        for message in messages[1:]
    ]
    assert [record["user"] for record in all_records if "user" in record] == [
        row[3] for row in originals[:12] if row[2] == "user"
    ]
    assert sum("previous_summary" in record for record in all_records) == 2
    assert (await message_rows(harness, harness.system))[: len(originals)] == originals
    assert [message["content"] for message in provider.answer_calls[-1][1:-1]] == [
        row[3] for row in originals[12:]
    ]


async def test_recent_cancelled_partial_code_stays_verbatim_after_compaction(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    ids = await seed(harness, harness.system)
    partial = "```python\ndef greet(name):\n    text = f'안녕하세요, {name}님'\n    "
    async with harness.database.session() as session:
        run = await session.get(GenerationRun, ids[-1])
        run.status, run.cancel_requested = "cancelled", True
        assistant = await session.get(Message, run.assistant_message_id)
        assistant.status, assistant.content = "cancelled", partial
        await session.commit()
    enable_compaction(harness)
    request = await harness.submit(content="이어서 말해")
    await harness.execute_next()
    assert (await snapshot(harness.database, request["id"])).run.status == "completed"
    context = provider.answer_calls[-1]
    assert context[-2] == {"role": "assistant", "content": partial}
    assert context[-1] == {"role": "user", "content": "이어서 말해"}
    assert "부분 답변" in context[0]["content"]
    assert "끝난 지점부터" in context[0]["content"]


@pytest.mark.parametrize("failure", ["missing_usage", "wrong_input_usage", "excess_output_usage"])
async def test_unconfirmed_summary_usage_preserves_history_without_member_charge(
    harness: Harness, failure: str
) -> None:
    provider = install_provider(harness)
    await harness.grant()
    await seed(harness, harness.member)
    originals = await message_rows(harness, harness.member)
    before = await balance(harness.database, harness.member)
    answer_count = len(provider.answer_calls)
    enable_compaction(harness)
    provider.summary_mode = failure

    request = await harness.submit(harness.member, content="사용량 오류가 발생할 질문")
    await harness.execute_next()
    result = await snapshot(harness.database, request["id"])
    summaries = await attempts(harness, harness.member)
    assert result.run.status == "failed"
    assert result.run.error_code == "context_compaction_failed"
    assert result.assistant.content == ""
    assert result.reservation.status == "released"
    assert result.reservation.usage_basis == "waived"
    assert len(provider.answer_calls) == answer_count
    assert provider.summary_closed.is_set()
    assert (await balance(harness.database, harness.member)).used_tokens == before.used_tokens
    assert (await message_rows(harness, harness.member))[: len(originals)] == originals
    assert await checkpoint(harness, harness.member) == (None, 0)
    assert len(summaries) == 1 and summaries[0].status == "failed"
    assert summaries[0].content is None
    # 불일치한 최종 값 대신 별도 유지 기록에는 확인된 수신량만 남긴다.
    assert summaries[0].usage_basis == "received"
    assert summaries[0].input_tokens == 700
    assert summaries[0].output_tokens == 12

    provider.summary_mode = "normal"
    following = await harness.submit(harness.member, content="정상 모델로 다시 질문")
    await harness.execute_next()
    assert (await snapshot(harness.database, following["id"])).run.status == "completed"


async def test_insufficient_budget_after_summary_keeps_checkpoint_without_charging(
    harness: Harness,
) -> None:
    provider = install_provider(harness)
    await harness.grant(limit=1_740)
    await seed(harness, harness.member)
    before = await balance(harness.database, harness.member)
    assert before.remaining_tokens == 400
    answer_count = len(provider.answer_calls)
    enable_compaction(harness)

    request = await harness.submit(harness.member, content="압축 후 예산 부족 질문")
    await harness.execute_next()
    result = await snapshot(harness.database, request["id"])
    after = await balance(harness.database, harness.member)
    assert result.run.status == "failed"
    assert result.run.error_code == "context_compaction_limit"
    assert result.reservation.status == "released"
    assert result.assistant.content == ""
    assert len(provider.answer_calls) == answer_count
    assert after.used_tokens == before.used_tokens
    assert after.remaining_tokens == 400 and after.reserved_tokens == 0
    assert (await checkpoint(harness, harness.member))[1] == 4
    assert (await attempts(harness, harness.member))[0].status == "completed"
    # 유효한 요약은 다음에 재사용할 수 있게 남기고 답변을 시작하지 않는다.
    assert sum(event.kind == "error" for event in result.events) == 1
