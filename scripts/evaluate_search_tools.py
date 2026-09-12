#!/usr/bin/env python3
"""실행 중인 로컬 MLX에서 합성 검색 판단·사용량·스트림 중단을 검사한다.

외부 검색·사용자 DB 접근·모델 서버 시작은 하지 않는다.
"""

import asyncio
import json
import sys
from contextlib import aclosing
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.llm.providers.mlx import MlxServerProvider
from backend.app.schemas import ChatMessage, GenerationOptions
from backend.app.tools.web_search.planning import PLANNING_PROMPT, SEARCH_TOOLS, validate_call


def messages(entries, sources):
    return [
        ChatMessage(role="system", content=PLANNING_PROMPT),
        ChatMessage(
            role="user",
            content=json.dumps(
                {"entries": entries, "sources": sources, "previous_queries": []}, ensure_ascii=False
            ),
        ),
    ]


async def main() -> None:
    settings = Settings()
    if urlsplit(settings.llm_base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("검증은 이미 실행 중인 loopback 모델 서버에서만 허용합니다.")
    provider = MlxServerProvider(settings)
    options = GenerationOptions(thinking=False, max_tokens=256)
    cases = [
        ({0: "그 회사 최신 뉴스를 검색해줘", 1: "삼성전자에 대해 알려줘"}, [], "삼성전자"),
        (
            {0: "가상 검증 패키지 TestAlpha의 최신 버전을 검색해줘"},
            [
                {
                    "title": "가상 공식 릴리스 기록",
                    "snippet": (
                        "합성 평가 자료: TestAlpha의 최신 버전은 9.9.9다. "
                        "이 자료는 해당 질문의 최신 버전을 직접 확인한다."
                    ),
                }
            ],
            None,
        ),
    ]
    for index, (entries, sources, expected) in enumerate(cases, 1):
        prompt = messages(entries, sources)
        async with asyncio.timeout(90):
            counted = await provider.count_tools(prompt, options, SEARCH_TOOLS)
            final = None
            async for delta in provider.stream_tools(prompt, options, SEARCH_TOOLS):
                if delta.final:
                    final = delta
            assert final and final.input_tokens == counted and len(final.tool_calls) == 1
            assert (
                type(final.output_tokens) is int and 0 <= final.output_tokens <= options.max_tokens
            )
            query = validate_call(final.tool_calls[0], entries, 500)
            assert (expected in query) if expected else query is None
            print(
                json.dumps(
                    {
                        "case": index,
                        "input_tokens": counted,
                        "output_tokens": final.output_tokens,
                        "tool": final.tool_calls[0].name,
                        "query": query,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    # 첫 실제 토큰 뒤 연결을 닫고 다음 입력 계산이 가능해야 한다.
    async with asyncio.timeout(90):
        prompt = messages(cases[0][0], [])
        async with aclosing(provider.stream_tools(prompt, options, SEARCH_TOOLS)) as stream:
            async for delta in stream:
                if delta.received_output_tokens:
                    assert not delta.final
                    break
            else:
                raise AssertionError("중단 검증에 필요한 실제 토큰 조각이 없습니다.")
        assert await provider.count_tools(prompt, options, SEARCH_TOOLS) > 0
    print("스트림 중단·후속 입력 계산 통과", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
