from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    thinking: bool = False
    max_tokens: int = Field(default=4_096, ge=1, le=4_096)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    options: GenerationOptions = Field(default_factory=GenerationOptions)


class ProviderStatus(BaseModel):
    backend: str
    ready: bool
    model: str
    detail: str


class StatusResponse(BaseModel):
    gateway: Literal["online"] = "online"
    provider: ProviderStatus
