from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    """환경변수 또는 로컬 .env 파일에서 읽는 실행 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "Qwen Workbench API"
    llm_backend: Literal["mlx", "mock"] = "mlx"
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model_id: str = "mlx-community/Qwen3.8-27B-4bit"
    llm_context_window: int = Field(default=32_768, ge=1_024, le=262_144)
    llm_max_concurrent_generations: int = Field(default=1, ge=1, le=8)
    llm_request_timeout_seconds: float = Field(default=900, ge=10, le=3_600)
    llm_max_history_chars: int = Field(default=200_000, ge=1_000, le=2_000_000)
    llm_compaction_trigger_ratio: float = Field(default=0.75, ge=0.5, le=0.95)
    llm_compaction_target_ratio: float = Field(default=0.55, ge=0.2, le=0.8)
    llm_compaction_keep_turns: int = Field(default=4, ge=1, le=20)
    llm_compaction_max_tokens: int = Field(default=1_024, ge=128, le=4_096)
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # API 내장 실행자는 호환용으로만 사용하고 기본 실행은 독립 프로세스가 담당한다.
    generation_worker_enabled: bool = False
    generation_queue_limit: int = Field(default=3, ge=1, le=20)
    web_search_provider: Literal["disabled", "tavily", "brave"] = "tavily"
    web_search_api_key: SecretStr | None = None
    web_search_timeout_seconds: float = Field(default=8, ge=1, le=30)
    web_search_max_results: int = Field(default=5, ge=1, le=5)
    web_search_max_query_chars: int = Field(default=500, ge=20, le=1000)
    web_search_max_context_chars: int = Field(default=12000, ge=500, le=30000)
    web_search_check_cache_seconds: float = Field(default=30, ge=0, le=300)
    web_search_check_timeout_seconds: float = Field(default=2, ge=0.1, le=5)
    # 실제 모델의 도구 호출 검증을 마친 환경에서만 문맥 검색 반복을 활성화한다.
    context_recall_enabled: bool = True
    context_recall_max_results: int = Field(default=3, ge=1, le=5)
    context_recall_max_chars: int = Field(default=3600, ge=600, le=6000)
    memory_context_max_chars: int = Field(default=4000, ge=500, le=8000)
    file_rag_enabled: bool = True
    file_storage_path: Path = Path("data/documents")
    file_embedding_path: Path = Path("models/multilingual-e5-small")
    # 구조 검사는 항상 실행하며 ClamAV를 지정하면 검사 실패도 업로드 처리 실패로 닫는다.
    file_clamav_path: Path | None = None

    web_search_agent_enabled: bool = True
    web_search_max_attempts: int = Field(default=2, ge=1, le=3)
    web_search_agent_timeout_seconds: float = Field(default=90, ge=5, le=180)
    web_search_planning_max_tokens: int = Field(default=256, ge=64, le=512)
    generation_max_steps: int = Field(default=8, ge=3, le=12)
    generation_questions_enabled: bool = True
    database_enabled: bool = False
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=5, gt=0, le=60)
    database_connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    database_health_timeout_seconds: float = Field(default=2, gt=0, le=30)
    signup_mode: Literal["open", "disabled"] = "open"
    auth_cookie_secure: bool = False

    @model_validator(mode="before")
    @classmethod
    def protect_secrets(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        # 필드 검증 전에 감싸서 구조화된 검증 오류에서도 접속 정보와 키를 가린다.
        for name in ("database_url", "migration_database_url", "web_search_api_key"):
            secret = values.get(name)
            if isinstance(secret, str):
                values[name] = SecretStr(secret) if secret.strip() else None
        return values

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        # URL 파서 오류에는 인증정보가 포함될 수 있으므로 안전한 메시지만 노출한다.
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+asyncpg"
                and bool(url.host)
                and bool(url.database)
                and (url.port is None or 1 <= url.port <= 65_535)
            )
        except (ArgumentError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(
                "Database URL must use postgresql+asyncpg and include a host and database"
            ) from None
        return value

    @model_validator(mode="after")
    def require_enabled_database_url(self) -> Self:
        if self.database_enabled and self.database_url is None:
            raise ValueError("DATABASE_URL is required when DATABASE_ENABLED is true")
        if self.llm_compaction_target_ratio >= self.llm_compaction_trigger_ratio:
            raise ValueError("압축 목표 비율은 시작 비율보다 작아야 합니다.")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def auth_cookie_name(self) -> str:
        return "__Host-session" if self.auth_cookie_secure else "project_llm_session"


@lru_cache
def get_settings() -> Settings:
    return Settings()
