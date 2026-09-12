"""필수 정보가 부족할 때 한 번만 질문 카드를 만들고 일반 답변과 같은 한도로 처리한다."""

import asyncio
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from sqlalchemy import select

from backend.app.context.dependencies import check_dependencies
from backend.app.llm.protocol import ProviderDelta, ProviderUnavailable, ToolChatProvider
from backend.app.models import GenerationRun
from backend.app.repositories import Repository
from backend.app.runtime.cancellation import GenerationCancelled, cancellable
from backend.app.runtime.contracts import GenerationJob, ModelExecution
from backend.app.runtime.steps import StepService
from backend.app.schemas import ChatMessage, GenerationOptions

QuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
ChoiceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
AnswerText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    question: QuestionText
    options: list[ChoiceText] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def distinct_options(self):
        if len(self.options) == 1 or len(set(self.options)) != len(self.options):
            raise ValueError("선택지는 없거나 서로 다른 2~4개여야 합니다.")
        return self


class QuestionCard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    questions: list[Question] = Field(min_length=1, max_length=3)


class QuestionAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    answers: list[AnswerText] = Field(min_length=1, max_length=3)


QUESTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ask_user_question",
            "description": "답변에 필요한 누락 정보를 묻습니다. 직접 입력도 가능합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "maxLength": 300},
                                "options": {
                                    "type": "array",
                                    "maxItems": 4,
                                    "items": {"type": "string", "maxLength": 100},
                                },
                            },
                            "required": ["question", "options"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
        },
    }
]

QUESTION_PROMPT = (
    "대부분의 요청에는 바로 답하세요. 이미 대화에 있는 정보를 다시 묻지 마세요. "
    "답변 결과를 크게 바꾸는 필수 정보가 빠져 안전한 가정으로도 진행할 수 없을 때만 "
    "ask_user_question을 한 번 호출하세요. 질문은 최대 3개, 선택지는 없거나 서로 다른 "
    "2~4개이며 직접 입력도 가능합니다. 비밀번호·인증 토큰은 묻지 마세요. "
    "도구 호출 전후에 질문을 본문으로 중복 출력하지 마세요. 질문 응답은 정보 제공이며 "
    "외부 검색이나 다른 도구 권한의 허가가 아닙니다. 사용자에게 질문이 필요 없으면 "
    "도구를 호출하지 말고 일반 답변을 작성하세요."
)


def parse_question_card(delta: ProviderDelta) -> dict | None:
    if not delta.tool_calls and delta.finish_reason != "tool_calls":
        return None
    if (
        delta.finish_reason != "tool_calls"
        or len(delta.tool_calls) != 1
        or delta.tool_calls[0].name != "ask_user_question"
    ):
        raise ProviderUnavailable("질문 카드 형식이 올바르지 않습니다.")
    try:
        return QuestionCard.model_validate_json(delta.tool_calls[0].arguments).model_dump()
    except ValidationError:
        raise ProviderUnavailable("질문 카드 형식이 올바르지 않습니다.") from None


def question_text(card: dict) -> str:
    # 고정 언어의 머리말 없이 모델이 작성한 질문의 언어를 그대로 보존한다.
    return "\n".join(
        f"{index + 1}. {item['question']}" for index, item in enumerate(card["questions"])
    )


async def prepare_questions(
    execution: ModelExecution, job: GenerationJob, cancellation: asyncio.Task
) -> tuple[GenerationJob, bool]:
    provider = execution.provider
    if not execution.settings.generation_questions_enabled or not isinstance(
        provider, ToolChatProvider
    ):
        return job, False
    base = [ChatMessage.model_validate(value) for value in job["messages"]]
    messages = [base[0], ChatMessage(role="system", content=QUESTION_PROMPT), *base[1:]]
    options = GenerationOptions.model_validate(job["options"])
    if sum(len(item.content) for item in messages) > execution.settings.llm_max_history_chars:
        return job, False
    try:
        tokens = await cancellable(
            provider.count_tools(messages, options, QUESTION_TOOLS), cancellation
        )
    except ProviderUnavailable:
        # 질문 도구의 tokenizer를 지원하지 않는 제공자는 기존 일반 답변으로 이어간다.
        return job, False
    cap = min(
        await StepService(execution.database, execution.settings).remaining(job),
        execution.settings.llm_context_window,
    )
    if type(tokens) is not int or tokens < 1 or tokens + min(256, options.max_tokens) > cap:
        return job, False
    options = options.model_copy(update={"max_tokens": min(options.max_tokens, cap - tokens)})
    async with execution.database.session() as session:
        await Repository(session, job["user_id"]).get_conversation(
            job["workspace_id"], job["conversation_id"]
        )
        run = await session.scalar(
            select(GenerationRun).where(GenerationRun.id == job["id"]).with_for_update()
        )
        if (
            run.cancel_requested
            or run.status != "running"
            or not await check_dependencies(session, run.memory_dependencies)
        ):
            raise GenerationCancelled
        run.prompt_tokens, run.max_output_tokens, run.thinking = (
            tokens,
            options.max_tokens,
            options.thinking,
        )
        await session.commit()
    return {
        **job,
        "messages": [item.model_dump() for item in messages],
        "prompt_tokens": tokens,
        "options": {"thinking": options.thinking, "max_tokens": options.max_tokens},
    }, True
