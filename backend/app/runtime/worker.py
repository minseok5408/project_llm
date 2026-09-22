"""API 수명과 독립적으로 영속 생성 대기열을 처리하는 단일 실행자."""

import asyncio
import logging
from contextlib import aclosing
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy import select
from sqlalchemy.engine import make_url

from backend.app.context.local import LocalContextService
from backend.app.context.service import CompactionService
from backend.app.context.status import context_status
from backend.app.files.context import FileContextService
from backend.app.files.indexing import FileIndexer
from backend.app.llm.protocol import ProviderUnavailable
from backend.app.models import GenerationRun
from backend.app.repositories import AccessDenied, InvalidInput, Repository
from backend.app.runtime.cancellation import GenerationCancelled
from backend.app.runtime.contracts import GenerationJob, ModelExecution
from backend.app.runtime.progress import advance_progress
from backend.app.runtime.steps import StepLimitExceeded, StepService
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.services.generations import GenerationService
from backend.app.services.generations.events import add_event, now
from backend.app.services.token_quota import QuotaExceeded
from backend.app.tools.calculation.service import CalculationService
from backend.app.tools.questions import (
    QUESTION_TOOLS,
    parse_question_card,
    prepare_questions,
    question_text,
)
from backend.app.tools.web_search.service import WebSearchService

logger = logging.getLogger(__name__)
WORKER_LOCK = 7160524630128423


class GenerationWorker:
    """PostgreSQL 세션 잠금으로 프로세스 전체에서 실행자 한 개만 추론을 담당한다."""

    def __init__(self, service: GenerationService):
        self.service = service
        self.files = FileIndexer(service.database, service.settings)
        self.task: asyncio.Task | None = None
        self.connection: asyncpg.Connection | None = None
        self.worker_id = uuid4()
        self.current_generation_id: UUID | None = None
        self.heartbeat_task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task is not None and not self.task.done():
            raise RuntimeError("이미 실행 중인 생성 실행자입니다.")
        self.task = asyncio.create_task(self.run(), name="generation-worker")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def claim(self) -> GenerationJob | None:
        database = self.service.database
        async with database.session() as session:
            run = await session.scalar(
                select(GenerationRun)
                .where(GenerationRun.status == "queued")
                .order_by(GenerationRun.created_at, GenerationRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            run.status, run.started_at = "running", now()
            advance_progress(run, "context", "running")
            add_event(session, run, "meta", {"status": "running", "progress": run.progress})
            result: GenerationJob = {
                "id": run.id,
                "messages": list(run.request_messages),
                "options": {"thinking": run.thinking, "max_tokens": run.max_output_tokens},
                "user_id": run.user_id,
                "workspace_id": run.workspace_id,
                "conversation_id": run.conversation_id,
                "prompt_tokens": run.prompt_tokens,
                "memory_dependencies": dict(run.memory_dependencies),
                "context_compaction_needed": run.context_compaction_needed,
                "network_mode": run.network_mode,
                "network_revision": run.network_revision,
                "web_search_mode": run.web_search_mode,
            }
            await session.commit()
            return result

    def _model_execution(self) -> ModelExecution:
        # 작업 시작 시 현재 공급자를 가져와 교체된 모델이 다음 실행에 반영되게 한다.
        return ModelExecution(self.service.database, self.service.provider, self.service.settings)

    def _web_search(self, execution: ModelExecution) -> WebSearchService:
        return WebSearchService(
            execution,
            search_provider=self.service.search_provider,
            network_mode=self.service.network_mode,
        )

    async def execute(self, job: GenerationJob) -> None:
        execution = self._model_execution()
        ledger = StepService(execution.database, execution.settings)
        run_id, buffer, usage = job["id"], "", None
        last_flush, offset = monotonic(), 0
        received_output_tokens = 0
        answer_started = False
        cancelled = False
        finish_reason = None
        question_card = None
        signal = self.service.cancel_events.setdefault(run_id, asyncio.Event())

        async def wait_for_cancel():
            while not signal.is_set():
                self.check_heartbeat()
                if self.connection is not None and self.connection.is_closed():
                    raise ProviderUnavailable("생성 실행자 연결이 끊어졌습니다.")
                # 다른 API 프로세스의 중단도 감지하되 대기 중 DB 연결을 점유하지 않는다.
                async with self.service.database.session() as session:
                    requested = await session.scalar(
                        select(GenerationRun.cancel_requested).where(GenerationRun.id == run_id)
                    )
                if requested:
                    return
                try:
                    await asyncio.wait_for(signal.wait(), timeout=0.1)
                except TimeoutError:
                    pass

        cancellation = asyncio.create_task(wait_for_cancel())

        async def record_progress(key: str, status: str):
            async with self.service.database.session() as session:
                run = await session.scalar(
                    select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
                )
                if run.cancel_requested or run.status != "running":
                    raise GenerationCancelled
                advance_progress(run, key, status, name=key)
                add_event(session, run, "meta", {"progress": run.progress})
                await session.commit()

        def settlement():
            if usage is not None:
                return *usage, "provider"
            if cancelled:
                if received_output_tokens:
                    return job["prompt_tokens"], received_output_tokens, "received"
                return 0, 0, "waived"
            return None, None, "provider"

        async def flush_buffer():
            nonlocal buffer, offset, last_flush
            if buffer:
                await self.service.append_chunk(run_id, buffer, offset=offset)
                offset += len(buffer)
                buffer = ""
            last_flush = monotonic()

        async def finish_error(code: str, *, never_started: bool = False):
            try:
                await flush_buffer()
            finally:
                input_tokens, output_tokens, basis = settlement()
                await self.service.finish(
                    run_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_basis=basis,
                    received_output_tokens=received_output_tokens,
                    error_code=code,
                    never_started=never_started or not answer_started,
                )

        try:
            # 대기 중 비활성화·소속 철회된 계정은 추론 실행 전에 다시 차단한다.
            async with self.service.database.session() as session:
                await Repository(session, job["user_id"]).get_conversation(
                    job["workspace_id"], job["conversation_id"]
                )
            if job.get("context_compaction_needed"):
                try:
                    await record_progress("compaction", "running")
                    job = await CompactionService(execution).prepare(job, cancellation)
                    await record_progress("compaction", "completed")
                except GenerationCancelled:
                    cancelled = True
                    await finish_error("user_cancelled")
                    return
                except (InvalidInput, QuotaExceeded):
                    await self.service.finish(
                        run_id,
                        never_started=True,
                        received_output_tokens=0,
                        error_code="context_compaction_limit",
                        error_message=(
                            "현재 질문에 사용할 문맥이나 잔여 토큰이 부족합니다. "
                            "질문 또는 출력 길이를 줄여 주세요. 토큰은 차감되지 않았습니다."
                        ),
                    )
                    return
                except ProviderUnavailable:
                    await self.service.finish(
                        run_id,
                        never_started=True,
                        received_output_tokens=0,
                        error_code="context_compaction_failed",
                        error_message=(
                            "이전 대화를 정리하지 못했습니다. 다시 질문해 주세요. "
                            "기존 대화는 보존되었으며 토큰은 차감되지 않았습니다."
                        ),
                    )
                    return
            try:
                job = await self._web_search(execution).prepare(job, cancellation)
                if cancellation.done():
                    cancellation.result()
                    raise GenerationCancelled
            except GenerationCancelled:
                cancelled = True
                await finish_error("user_cancelled", never_started=True)
                return
            job = await LocalContextService(execution).prepare(job, cancellation)
            job = await FileContextService(execution, self.files.embeddings).prepare(
                job, cancellation
            )
            job = await CalculationService(execution).prepare(job, cancellation)
            job, questions_enabled = await prepare_questions(execution, job, cancellation)
            messages = [ChatMessage.model_validate(message) for message in job["messages"]]
            options = GenerationOptions.model_validate(job["options"])
            async with self.service.database.session() as session:
                run = await session.scalar(
                    select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
                )
                context = await context_status(session, run)
                advance_progress(run, "context", "completed")
                add_event(session, run, "meta", {"progress": run.progress})
                if context is not None:
                    context.update(
                        phase="ready",
                        input_tokens=run.prompt_tokens,
                        max_output_tokens=run.max_output_tokens,
                        context_window=self.service.settings.llm_context_window,
                    )
                    add_event(session, run, "meta", {"stage": "generating", "context": context})
                await session.commit()
            answer_step = await ledger.start(
                job,
                kind="llm",
                name="answer",
                prompt_tokens=job["prompt_tokens"],
                max_output_tokens=options.max_tokens,
            )
            answer_started = True
            answer_stream = (
                execution.provider.stream_tools(messages, options, QUESTION_TOOLS)
                if questions_enabled
                else execution.provider.stream(messages, options)
            )
            async with aclosing(answer_stream) as stream:
                pending = None
                try:
                    while True:
                        pending = asyncio.create_task(anext(stream))
                        completed, _ = await asyncio.wait(
                            (pending, cancellation), return_when=asyncio.FIRST_COMPLETED
                        )
                        if cancellation in completed:
                            cancellation.result()
                            cancelled = True
                        if pending in completed:
                            try:
                                delta = pending.result()
                            except StopAsyncIteration:
                                break
                            if self.connection is not None and self.connection.is_closed():
                                raise ProviderUnavailable("생성 실행자 연결이 끊어졌습니다.")
                            count = delta.received_output_tokens
                            if type(count) is int and 0 <= count <= options.max_tokens:
                                received_output_tokens = max(received_output_tokens, count)
                            if delta.final:
                                usage = (delta.input_tokens, delta.output_tokens)
                                finish_reason = delta.finish_reason
                                # 종료 이벤트 전에 확정 사용량을 영속화해 복구 시 보존한다.
                                await ledger.close(
                                    job,
                                    answer_step,
                                    status="completed",
                                    input_tokens=delta.input_tokens,
                                    output_tokens=delta.output_tokens,
                                    received_output_tokens=received_output_tokens,
                                )
                                if questions_enabled and not cancelled:
                                    question_card = parse_question_card(delta)
                                    if question_card:
                                        buffer += (
                                            "\n\n" if offset or buffer else ""
                                        ) + question_text(question_card)
                            if not cancelled:
                                buffer += delta.text
                                if (
                                    len(buffer) >= 4096
                                    or monotonic() - last_flush >= 0.04
                                    or delta.final
                                ):
                                    await flush_buffer()
                        if cancelled:
                            break
                finally:
                    # 다음 토큰을 기다리는 HTTP 읽기를 먼저 취소해야 연결을 즉시 닫을 수 있다.
                    if pending is not None:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            await flush_buffer()
            # 전체 생성량을 추정하지 않고 확인된 수신량만 청구하며 나머지는 면제한다.
            input_tokens, output_tokens, usage_basis = settlement()
            await self.service.finish(
                run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_basis=usage_basis,
                received_output_tokens=received_output_tokens,
                finish_reason=finish_reason,
                question_card=question_card,
            )
        except asyncio.CancelledError:
            # 취소 직전 커밋한 청크는 offset으로 중복 저장하지 않고 확정 사용량도 보존한다.
            await asyncio.shield(finish_error("worker_stopped"))
            raise
        except AccessDenied:
            await finish_error("access_revoked", never_started=True)
        except GenerationCancelled:
            cancelled = True
            await finish_error("user_cancelled")
        except StepLimitExceeded:
            await finish_error("step_limit", never_started=not answer_started)
        except ProviderUnavailable as error:
            await finish_error("provider_unavailable", never_started=not error.request_started)
        except Exception as error:
            logger.warning("생성 처리 실패", extra={"error_type": type(error).__name__})
            await finish_error("generation_failed")
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            self.service.cancel_events.pop(run_id, None)

    async def recover(self) -> None:
        execution = self._model_execution()
        async with self.service.database.session() as session:
            ids = list(
                (
                    await session.scalars(
                        select(GenerationRun.id).where(GenerationRun.status == "running")
                    )
                ).all()
            )
        # 이전 실행자의 DB 잠금이 사라진 뒤에도 모델 실제 사용량은 추측하지 않는다.
        for run_id in ids:
            await self.service.finish(run_id, error_code="worker_interrupted")
        await CompactionService(execution).recover()
        await self._web_search(execution).recover()
        await self.files.recover()
        await self.service.recover_unsettled()

    def check_heartbeat(self) -> None:
        if self.heartbeat_task is not None and self.heartbeat_task.done():
            # 관측 기록 실패도 연결 장애로 취급하며 새 리더의 추론과 겹치지 않게 닫는다.
            self.heartbeat_task.result()
            raise ProviderUnavailable("생성 실행자 상태 기록이 종료되었습니다.")

    async def heartbeat(self) -> None:
        while True:
            await self.connection.execute(
                "UPDATE worker_heartbeats SET heartbeat_at = clock_timestamp(), "
                "generation_id = $2 WHERE name = 'generation' AND worker_id = $1",
                self.worker_id,
                self.current_generation_id,
                timeout=3,
            )
            await asyncio.sleep(2)

    async def run(self) -> None:
        while True:
            leader = False
            try:
                settings = self.service.settings
                url = make_url(settings.database_url.get_secret_value()).set(
                    drivername="postgresql"
                )
                # API 풀과 별도 연결 하나를 써서 작은 풀에서도 SSE/업무 요청을 막지 않는다.
                self.connection = await asyncpg.connect(
                    url.render_as_string(hide_password=False),
                    timeout=5,
                    server_settings={"timezone": "UTC"},
                )
                leader = await self.connection.fetchval(
                    "SELECT pg_try_advisory_lock($1)", WORKER_LOCK
                )
                if leader:
                    # 리더 잠금의 연결 자체로 관측을 기록하므로 끊긴 실행자는 갱신할 수 없다.
                    await self.connection.execute(
                        "INSERT INTO worker_heartbeats "
                        "(name, worker_id, started_at, heartbeat_at) "
                        "VALUES ('generation', $1, clock_timestamp(), clock_timestamp()) "
                        "ON CONFLICT (name) DO UPDATE SET worker_id = EXCLUDED.worker_id, "
                        "started_at = EXCLUDED.started_at, heartbeat_at = EXCLUDED.heartbeat_at, "
                        "stopped_at = NULL, generation_id = NULL",
                        self.worker_id,
                        timeout=3,
                    )
                    self.heartbeat_task = asyncio.create_task(
                        self.heartbeat(), name="generation-worker-heartbeat"
                    )
                    await self.recover()
                    while not self.connection.is_closed():
                        self.check_heartbeat()
                        job = await self.claim()
                        if job is None:
                            if not await self.files.process_next():
                                await asyncio.sleep(0.25)
                        else:
                            self.current_generation_id = job["id"]
                            try:
                                await self.execute(job)
                            finally:
                                self.current_generation_id = None
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "생성 실행자 재연결 대기", extra={"error_type": type(error).__name__}
                )
                await asyncio.sleep(2)
            finally:
                if self.heartbeat_task is not None:
                    self.heartbeat_task.cancel()
                    await asyncio.gather(self.heartbeat_task, return_exceptions=True)
                    self.heartbeat_task = None
                if self.connection is not None:
                    try:
                        if leader and not self.connection.is_closed():
                            await self.connection.execute(
                                "UPDATE worker_heartbeats SET stopped_at = clock_timestamp(), "
                                "generation_id = NULL WHERE name = 'generation' AND worker_id = $1",
                                self.worker_id,
                                timeout=2,
                            )
                    except Exception:
                        # DB 장애 시 오래된 상태로 남겨도 리더 판정은 세션 잠금만 사용한다.
                        pass
                    finally:
                        try:
                            await self.connection.close(timeout=2)
                        finally:
                            self.connection = None
