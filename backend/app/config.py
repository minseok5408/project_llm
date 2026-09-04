from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Qwen Workbench API"
    llm_backend: Literal["mlx", "mock"] = "mlx"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model_id: str = "mlx-community/Qwen3.8-27B-4bit"
    llm_context_window: int = Field(default=32_768, ge=1_024, le=262_144)
    llm_max_concurrent_generations: int = Field(default=1, ge=1, le=8)
    llm_request_timeout_seconds: float = Field(default=900, ge=10, le=3_600)
    llm_max_history_chars: int = Field(default=200_000, ge=1_000, le=2_000_000)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
