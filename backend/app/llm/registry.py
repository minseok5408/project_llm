"""설정에 맞는 로컬 모델 공급자를 선택한다."""

from backend.app.config import Settings
from backend.app.llm.protocol import ChatProvider
from backend.app.llm.providers.mlx import MlxServerProvider
from backend.app.llm.providers.mock import MockProvider


def build_provider(settings: Settings) -> ChatProvider:
    if settings.llm_backend == "mock":
        return MockProvider(settings)
    return MlxServerProvider(settings)
