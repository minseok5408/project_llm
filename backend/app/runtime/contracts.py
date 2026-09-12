"""생성 단계가 공유하는 작업 스냅샷과 모델 실행 의존성만 정의한다."""

from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import UUID

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.llm.protocol import ChatProvider


class MessageSnapshot(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class OptionsSnapshot(TypedDict):
    thinking: bool
    max_tokens: int


class MessageHistory(TypedDict):
    messages: list[MessageSnapshot]


class GenerationJob(MessageHistory):
    """실행자가 DB에서 인수하고 준비 단계가 새 값으로 반환하는 생성 작업."""

    id: UUID
    user_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    options: OptionsSnapshot
    prompt_tokens: int
    memory_dependencies: dict[str, int]
    context_compaction_needed: bool
    network_mode: Literal["auto", "local"]
    network_revision: int
    web_search_mode: Literal["auto", "on", "off"]


@dataclass(frozen=True, slots=True)
class ModelExecution:
    """모델 준비 단계에 필요한 의존성이며 생성 승인·중단·정산 기능은 포함하지 않는다."""

    database: Database
    provider: ChatProvider
    settings: Settings
